#!/usr/bin/env bash
# Container entrypoint: optionally opens an frp reverse tunnel, then runs the
# command it was given.
#
# The tunnel exists so a simulator running on a client's machine (behind NAT,
# no public IP) is reachable by Shinro, which pulls the assessment report over
# HTTP. frpc dials OUT to a relay we run, so nothing inbound needs opening on
# the client side.
#
# Entirely opt-in: with FRP_SERVER/FRP_TOKEN unset the container behaves exactly
# as it did before this script existed.
set -euo pipefail

if [ -n "${FRP_SERVER:-}" ] && [ -n "${FRP_TOKEN:-}" ]; then
  : "${FRP_SERVER_PORT:=7000}"
  : "${FRP_LOCAL_PORT:=9000}"
  : "${FRP_REMOTE_PORT:=9000}"
  : "${FRP_PROXY_NAME:=autosys-$(hostname)}"

  # localIP is 0.0.0.0 rather than 127.0.0.1: the API binds to 0.0.0.0 inside
  # the container, and frpc resolving "localhost" ahead of it has no advantage
  # here while breaking if the server ever binds only to the container IP.
  cat > /tmp/frpc.toml <<EOF
serverAddr = "${FRP_SERVER}"
serverPort = ${FRP_SERVER_PORT}
auth.token = "${FRP_TOKEN}"

# Keep retrying instead of exiting when the relay is unreachable at startup or
# drops mid-session -- this is the reconnect behaviour raw ssh -R lacks.
loginFailExit = false

[[proxies]]
name = "${FRP_PROXY_NAME}"
type = "tcp"
localIP = "127.0.0.1"
localPort = ${FRP_LOCAL_PORT}
remotePort = ${FRP_REMOTE_PORT}
EOF

  echo "[entrypoint] frp tunnel enabled -> ${FRP_SERVER}:${FRP_SERVER_PORT} (remote port ${FRP_REMOTE_PORT})"
  # Backgrounded deliberately: a failed tunnel must not take down the simulator,
  # which is still fully usable locally without it.
  frpc -c /tmp/frpc.toml &
else
  echo "[entrypoint] frp tunnel disabled (set FRP_SERVER and FRP_TOKEN to enable)"
fi

exec "$@"
