# Simulator relay (frp)

How a client's locally-run AutoSys simulator becomes reachable by Shinro.

## The problem

Shinro pulls the assessment report over HTTP from a running simulator. On a real
engagement that simulator runs on the client's machine — behind NAT, no public
IP, nothing inbound reachable. Something has to bridge that gap.

Every option is a variant of the same shape: the client-side machine dials **out**
to a host we control, and traffic flows back through that connection. The question
was only what software should terminate it.

## What we chose

**frp** ([fatedier/frp](https://github.com/fatedier/frp)), with the client binary
`frpc` baked into the simulator image itself.

Client-side setup is one command — no tunnel tool to install, no key file, no
ports to open:

```
docker run -d --name autosys-clone -p 9000:9000 \
  -e AUTOSYS_AUTH_ENABLED=false \
  -e FRP_SERVER=<relay-ip> -e FRP_SERVER_PORT=7000 \
  -e FRP_TOKEN=<token> -e FRP_REMOTE_PORT=9000 \
  <image> autosys scheduler serve --host 0.0.0.0 --port 9000 --dry-run
```

`docker-entrypoint.sh` starts `frpc` only when `FRP_SERVER` and `FRP_TOKEN` are
both present, so the image behaves exactly as before when they're absent.

## Why frp and not a reverse SSH tunnel

A reverse SSH tunnel (`ssh -R 9000:localhost:9000 user@relay`) was the initial
proposal and is a legitimate way to solve this. We went the other way after
testing both approaches. The reasoning:

**1. "The client only needs SSH" doesn't survive contact with production.**
A bare `ssh -R` tunnel dies silently when the network blips and does not come
back. Making it production-viable requires `autossh` or a supervisor loop — and
`autossh` is not preinstalled on macOS or most distros. Once the client is
installing a tool either way, the "zero extra dependency" advantage is gone, and
`frpc` is no harder to install than `autossh`. With `frpc` shipped inside the
image, the client installs *nothing*.

**2. Credential delivery is a chat message, and tokens beat key files.**
Setup instructions reach the client through Shinro chat. Pasting a token string
into chat is trivial. Delivering a PEM private key that way — preserving
newlines, landing at the right path with `chmod 600` — is awkward and
error-prone. frp authenticates with a shared token.

**3. Reconnection is built in, and we verified it.**
Tested locally: killed the relay mid-session, and `frpc` retried on its own and
restored the tunnel in ~6 seconds with no container restart and no human
intervention. That is the exact failure mode raw `ssh -R` cannot handle alone.

**4. Smaller, purpose-specific attack surface.**
`frps` exposes one port that does one thing: forward TCP for authenticated
clients. `sshd` on a public IP is a general-purpose remote shell — a broader
surface needing `GatewayPorts` changes to a config that also governs admin
access, restricted `authorized_keys` entries per client, fail2ban, and a key
rotation process.

**5. Multi-client works without bookkeeping.**
SSH reverse tunnels need a hand-assigned relay port per client. frp allocates
from a configured range and identifies clients by proxy name. This doesn't
matter at one concurrent engagement; it matters at the second.

**Latency was not a deciding factor.** Both approaches are TCP relays through the
same extra hop, so the geographic cost is identical. The workload is ~85KB per
assessment against LLM calls that take 12–21s — tunnel overhead is a rounding
error. (frp does additionally support QUIC/KCP transports that mitigate
TCP-over-TCP head-of-line blocking on lossy links; SSH cannot.)

### When to revisit this

Re-evaluate if any of these become true:

- More than a handful of concurrent client tunnels — consider frp's `type = "http"`
  proxies with subdomain routing instead of TCP port allocation (needs a wildcard
  DNS record pointing at the relay)
- Clients on lossy links — switch the transport to QUIC/KCP
- The relay becomes engagement-critical — it's currently a single point of
  failure with no HA

## Deploying

```sh
cp frps.toml.example frps.toml     # set a real auth.token
docker compose up -d
```

Security group on the relay host:

| Port        | Source              | Why                                    |
|-------------|---------------------|----------------------------------------|
| 7000/tcp    | anywhere            | clients' `frpc` connects here          |
| 9000–9100/tcp | Shinro's egress IP only | forwarded simulator ports       |

**Do not open 9000–9100 to the world.** A tunnelled simulator runs with
`AUTOSYS_AUTH_ENABLED=false`, so anything that can reach the forwarded port has
full API access to the client's job data.

Shinro reaches a tunnelled simulator at `http://<relay-ip>:<forwarded-port>`,
configured via `SIMULATOR_RELAY_*` settings in Shinro's `apps/api/.env`.

## Open blockers

1. **No relay host yet.** Needs a public-IP host to run `frps`. Vishy has an EC2
   used to demo this pattern; we need its IP and deploy access, or a new
   instance. The AWS key currently in Shinro's `.env` is scoped to Bedrock and S3
   only — `ec2:DescribeInstances` is denied — so it can't provision one.

2. **Docker Hub is a personal account.** The image is published under an
   individual's namespace because there's no `quest1codes` Docker Hub org yet.
   Same bus-factor problem as the GHCR path under `raghav-quest1`: move it to an
   org before this is client-facing.

3. **`AUTOSYS_AUTH_ENABLED=false` is a deliberate shortcut.** The simulator's
   auth defaults to on but with a well-known JWT secret
   (`dev-secret-change-in-production`), and validation is pure HMAC against it —
   so anyone who knows the default can forge an admin token. Auth-on-with-default
   is barely better than auth-off while being one more thing to explain, so the
   instructions disable it and rely on network scoping instead. Proper fix: a
   per-run random secret, plus passing a real credential to Shinro's
   `run_autosys_assessment_tool` (which already accepts `auth_token`).

4. **Single fixed forwarded port.** `SIMULATOR_RELAY_REMOTE_PORT` is one value,
   so two concurrent client tunnels would collide. Fine while engagements are
   sequential; needs per-conversation port allocation before they aren't.

5. **amd64 only in practice.** The Dockerfile takes `TARGETARCH` and builds
   multi-arch under `buildx`, but published tags should be confirmed to include
   `linux/arm64` for clients on Apple Silicon.
