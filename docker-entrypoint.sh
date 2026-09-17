#!/usr/bin/env bash
# Container entrypoint: optionally opens an frp STCP tunnel, then runs the
# command it was given.
#
# The tunnel exists so a simulator running on a client's machine (behind NAT,
# no public IP) is reachable by Shinro, which pulls the assessment report over
# HTTP. frpc dials OUT to a relay we run, so nothing inbound needs opening on
# the client side.
#
# STCP rather than a plain forwarded TCP port: a forwarded port needs the
# relay's security group to allowlist the client's IP, and a client behind
# carrier-grade NAT can silently move to a different address mid-session,
# breaking any fixed CIDR rule. STCP opens no public data port at all -- the
# proxy here and Shinro's visitor (tools/frp_visitor in the Shinro repo) pair
# through the relay's control port using a shared secretKey, so there is
# nothing to allowlist and nothing world-reachable regardless of the client's
# network.
#
# Entirely opt-in: with FRP_SERVER/FRP_TOKEN/FRP_STCP_KEY unset the container
# behaves exactly as it did before this script existed.
set -euo pipefail

if [ -n "${FRP_SERVER:-}" ] && [ -n "${FRP_TOKEN:-}" ] && [ -n "${FRP_STCP_KEY:-}" ]; then
  : "${FRP_SERVER_PORT:=7000}"
  : "${FRP_LOCAL_PORT:=9000}"
  : "${FRP_PROXY_NAME:=autosys-sim}"

  # localIP is 127.0.0.1, not the container's external-facing address: the API
  # binds to 0.0.0.0 inside the container, which accepts connections via any
  # local interface including loopback, and frpc runs in the same container/
  # network namespace so loopback always reaches it.
  cat > /tmp/frpc.toml <<EOF
serverAddr = "${FRP_SERVER}"
serverPort = ${FRP_SERVER_PORT}
auth.token = "${FRP_TOKEN}"

# Keep retrying instead of exiting when the relay is unreachable at startup or
# drops mid-session -- this is the reconnect behaviour raw ssh -R lacks.
loginFailExit = false

[[proxies]]
name = "${FRP_PROXY_NAME}"
type = "stcp"
secretKey = "${FRP_STCP_KEY}"
localIP = "127.0.0.1"
localPort = ${FRP_LOCAL_PORT}
EOF

  echo "[entrypoint] frp STCP tunnel enabled -> ${FRP_SERVER}:${FRP_SERVER_PORT} (proxy '${FRP_PROXY_NAME}')"
  # Backgrounded deliberately: a failed tunnel must not take down the simulator,
  # which is still fully usable locally without it.
  frpc -c /tmp/frpc.toml &
else
  echo "[entrypoint] frp tunnel disabled (set FRP_SERVER, FRP_TOKEN and FRP_STCP_KEY to enable)"
fi

exec "$@"
