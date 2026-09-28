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

# tcpMux is on by default, and when it is, frp's application-level heartbeat
# (transport.heartbeatInterval/heartbeatTimeout) is not sent at all -- the
# tcp-mux (yamux) layer's own keepalive is what actually keeps the control
# connection alive, controlled by tcpMuxKeepaliveInterval (default 30s).
# Confirmed in testing: with the 30s default, some clients' NAT/carrier-NAT
# idle-connection timeout kills the TCP connection before the next keepalive,
# and the tunnel cycles fully offline and back on a ~90s clock forever.
# Sending one every 5s keeps traffic flowing often enough that most NATs
# never consider the connection idle in the first place.
transport.tcpMuxKeepaliveInterval = 5

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

# No command given: this is the client-facing `docker run <image>` path --
# start everything a real engagement needs from the one container: PostgreSQL
# (see below -- there is no separate DB container to pair with a one-line
# `docker run`, and 300k+ files means many concurrent writers, which SQLite's
# single-writer model cannot do), the assessment API + EPS (9000, what the frp
# tunnel above exposes to Shinro), the WCC dashboard backend (127.0.0.1-only --
# nginx is the only thing that talks to it), and nginx (8080, the client's own
# browser talks to this and only this to log in and load their JIL files). A
# command IS still honoured below for the multi-container compose files in
# this repo, which run `serve`/`wcc` as separate containers against their own
# separate `postgres` service -- none of the bootstrapping below runs there.
if [ "$#" -eq 0 ]; then
  PIDS=()
  PG_STARTED=""
  cleanup() {
    trap - TERM INT
    for pid in "${PIDS[@]}"; do
      kill "$pid" 2>/dev/null || true
    done
    if [ -n "$PG_STARTED" ]; then
      su postgres -c "'${PG_BIN}/pg_ctl' -D '${PGDATA}' -m fast stop" 2>/dev/null || true
    fi
  }
  trap cleanup TERM INT

  # ---------------------------------------------------------------------
  # Bundled PostgreSQL. Data lives under the same /app/data volume every
  # other piece of state already uses, so one `docker run -v` is still
  # enough to persist everything across restarts.
  # ---------------------------------------------------------------------
  PG_BIN="$(dirname "$(find /usr/lib/postgresql -maxdepth 3 -name initdb | head -n1)")"
  PGDATA=/app/data/pgdata
  PG_PASSWORD="${AUTOSYS_DB_PASSWORD:-autosys}"

  mkdir -p "$PGDATA"
  chown -R postgres:postgres /app/data

  if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "[entrypoint] initializing PostgreSQL data directory"
    su postgres -c "'${PG_BIN}/initdb' -D '${PGDATA}' -U postgres --auth=trust" \
      > /tmp/initdb.log 2>&1 \
      || { echo "[entrypoint] initdb failed:"; cat /tmp/initdb.log; exit 1; }
  fi

  echo "[entrypoint] starting PostgreSQL"
  su postgres -c "'${PG_BIN}/pg_ctl' -D '${PGDATA}' -l /tmp/postgres.log -w -o '-c listen_addresses=localhost' start"
  PG_STARTED=1

  for i in $(seq 1 30); do
    su postgres -c "'${PG_BIN}/pg_isready' -q" && break
    sleep 1
  done

  su postgres -c "'${PG_BIN}/psql' -U postgres -tAc \"SELECT 1 FROM pg_roles WHERE rolname='autosys'\"" \
    | grep -q 1 \
    || su postgres -c "'${PG_BIN}/psql' -U postgres -c \"CREATE ROLE autosys LOGIN PASSWORD '${PG_PASSWORD}'\""
  su postgres -c "'${PG_BIN}/psql' -U postgres -tAc \"SELECT 1 FROM pg_database WHERE datname='autosys'\"" \
    | grep -q 1 \
    || su postgres -c "'${PG_BIN}/createdb' -U postgres -O autosys autosys"

  export AUTOSYS_DB_URL="postgresql+psycopg2://autosys:${PG_PASSWORD}@localhost:5432/autosys"

  # Every `autosys` invocation runs schema init (create_all_sync) via the CLI's
  # root callback before its subcommand -- starting serve and wcc in the same
  # instant would race two of these. Running one command synchronously first
  # does it exactly once.
  echo "[entrypoint] initializing database schema"
  autosys scheduler status >/dev/null 2>&1 || true

  echo "[entrypoint] starting AutoSys API server on :9000 (dry-run)"
  autosys scheduler serve --host 0.0.0.0 --port 9000 --dry-run &
  PIDS+=("$!")

  echo "[entrypoint] starting WCC dashboard backend on 127.0.0.1:8090"
  autosys scheduler wcc --host 127.0.0.1 --port 8090 &
  PIDS+=("$!")

  echo "[entrypoint] starting nginx on :8080 (WCC UI, JIL upload)"
  nginx -g "daemon off;" &
  PIDS+=("$!")

  set +e
  wait -n
  EXIT_CODE=$?
  set -e
  echo "[entrypoint] a service exited (code ${EXIT_CODE}) -- stopping the others"
  cleanup
  exit "${EXIT_CODE}"
fi

exec "$@"
