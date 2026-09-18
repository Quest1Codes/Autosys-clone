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

**frp** ([fatedier/frp](https://github.com/fatedier/frp)) in **STCP mode**, with
the client binary `frpc` baked into the simulator image itself.

Client-side setup is one command — no tunnel tool to install, no key file, no
ports to open:

```
docker run -d --name autosys-clone -p 9000:9000 \
  -e AUTOSYS_AUTH_ENABLED=false \
  -e FRP_SERVER=<relay-ip> -e FRP_SERVER_PORT=7000 \
  -e FRP_TOKEN=<token> -e FRP_STCP_KEY=<stcp-secret> \
  <image> autosys scheduler serve --host 0.0.0.0 --port 9000 --dry-run
```

`docker-entrypoint.sh` starts `frpc` only when `FRP_SERVER`, `FRP_TOKEN` and
`FRP_STCP_KEY` are all present, so the image behaves exactly as before when
they're absent.

**Why STCP and not a plain forwarded TCP port** (frp's `type = "tcp"`, which is
what an earlier version of this doc described): a forwarded port needs the
relay's security group to allowlist the client's IP. In testing, a client on
carrier-grade NAT moved to a different address mid-session — twice, within the
same test session — breaking the allowlist outright, and there is no CIDR range
that fixes this reliably since the pool's boundaries aren't known ahead of time.
STCP opens no public data port on the relay at all: the simulator registers a
proxy, and Shinro's own `frpc` **visitor** (`tools/frp_visitor/` in the Shinro
repo) pairs with it through the relay's control port using a shared
`secretKey`. The relay's security group now only needs `7000/tcp` open, for
auth — nothing to allowlist, and nothing world-reachable regardless of either
side's network.

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

**5. No CIDR to maintain, ever.**
SSH reverse tunnels (and frp's own plain `type = "tcp"` mode) need a relay
security group rule scoped to the client's IP. STCP needs none — nothing
inbound is opened for the proxy/visitor pairing at all, so there's no rule to
keep in sync with a client's network, including one that moves mid-session.

**Latency was not a deciding factor.** Both approaches are TCP relays through the
same extra hop, so the geographic cost is identical. The workload is ~85KB per
assessment against LLM calls that take 12–21s — tunnel overhead is a rounding
error. (frp does additionally support QUIC/KCP transports that mitigate
TCP-over-TCP head-of-line blocking on lossy links; SSH cannot.)

### When to revisit this

Re-evaluate if any of these become true:

- More than one concurrent client engagement — STCP as built here uses one
  fixed proxy name (`autosys-sim`) and one shared `secretKey`, so a second
  simultaneous client would collide with the first (see blocker #4 below)
- Clients on lossy links — switch the transport to QUIC/KCP
- The relay becomes engagement-critical — it's currently a single point of
  failure with no HA

## Deploying

```sh
cp frps.toml.example frps.toml     # set a real auth.token
docker compose up -d
```

No `allowPorts` or port-range configuration is needed for STCP — that setting
only applies to frp's plain `tcp`/`udp` proxy types, which this relay doesn't use.

Security group on the relay host:

| Port     | Source   | Why                                              |
|----------|----------|---------------------------------------------------|
| 7000/tcp | anywhere | both the client's `frpc` and Shinro's `frpc` visitor authenticate here |

That's the only port. Nothing else needs to be open — STCP proxies and
visitors never bind a public port on the relay.

Shinro reaches a tunnelled simulator at `http://frp-visitor:<bind-port>` — an
internal hostname on Shinro's own `shinro` docker network, not the relay's
public IP. `SIMULATOR_RELAY_*` and `SIMULATOR_STCP_KEY` in Shinro's
`apps/api/.env` configure both the client-facing `docker run` command and the
`frp-visitor` sidecar (`tools/frp_visitor/` in the Shinro repo) that does the
pairing.

## Open blockers

1. **Relay has no Elastic IP.** `frps` is live on a dedicated EC2 instance
   (`i-0df3fd45298346aa6`, `autosys-simulator-relay`), currently at
   `34.226.194.254`. The AWS account is at its Elastic IP allocation limit, so
   the instance runs on an ephemeral public IP — stopping/restarting it changes
   the address, and `SIMULATOR_RELAY_HOST` (Shinro's root `.env` and
   `apps/api/.env`) must be updated by hand afterward. Get an EIP freed up (or
   the limit raised) before relying on this surviving a reboot unattended.

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

4. **Single fixed proxy name and secret.** The simulator always registers as
   `autosys-sim` with one shared `SIMULATOR_STCP_KEY`, so a second concurrent
   client would collide with the first. Fine while engagements are sequential;
   needs a per-conversation proxy name and secret before they aren't.

5. **amd64 only in practice.** The Dockerfile takes `TARGETARCH` and builds
   multi-arch under `buildx`, but published tags should be confirmed to include
   `linux/arm64` for clients on Apple Silicon.
