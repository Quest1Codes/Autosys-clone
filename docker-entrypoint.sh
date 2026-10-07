#!/usr/bin/env bash
# Container entrypoint: with no command, starts everything the simulator
# needs in one container (below); otherwise runs the command it was given.
set -euo pipefail

# No command given: this is the client-facing `docker run <image>` path --
# start everything a real engagement needs from the one container: PostgreSQL
# (see below -- there is no separate DB container to pair with a one-line
# `docker run`, and 300k+ files means many concurrent writers, which SQLite's
# single-writer model cannot do), the assessment API + EPS (9000, what Shinro
# calls), the WCC dashboard backend (127.0.0.1-only --
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
      "${PG_BIN}/pg_ctl" -D "${PGDATA}" -m fast stop 2>/dev/null || true
    fi
  }
  trap cleanup TERM INT

  # ---------------------------------------------------------------------
  # Bundled PostgreSQL. Data lives under the same /app/data volume every
  # other piece of state already uses, so one `docker run -v` is still
  # enough to persist everything across restarts.
  #
  # Runs as the image's non-root user (UID 1001, see the Dockerfile) rather
  # than via `su postgres` from root: PostgreSQL only refuses to run as
  # root, and whoever owns the data directory can run it. The socket goes
  # to /tmp because /run/postgresql is root-owned.
  #
  # A volume created by an older image (initialized as the Debian `postgres`
  # user, while this entrypoint still ran as root) is owned by a different
  # UID and can't be opened -- recreate it (`docker volume rm`) or chown it
  # to 1001 once.
  # ---------------------------------------------------------------------
  PG_BIN="$(dirname "$(find /usr/lib/postgresql -maxdepth 3 -name initdb | head -n1)")"
  PGDATA=/app/data/pgdata
  PG_SOCKET_DIR=/tmp

  if [ ! -w /app/data ]; then
    echo "[entrypoint] /app/data is not writable by UID $(id -u) -- see the volume note in docker-entrypoint.sh"
    exit 1
  fi

  # Credentials: no fixed default password (static-analysis finding SEC-06).
  # Each one is generated once per volume, kept under /app/data with 0600
  # permissions, and reused on every restart. AUTOSYS_DB_PASSWORD, if set,
  # still wins for the app role.
  gen_secret() {
    local file="$1"
    if [ ! -s "$file" ]; then
      (umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$file")
    fi
    cat "$file"
  }
  PG_SUPER_PASSWORD="$(gen_secret /app/data/.pg-superuser-password)"
  PG_PASSWORD="${AUTOSYS_DB_PASSWORD:-$(gen_secret /app/data/.autosys-db-password)}"
  export PGPASSWORD="$PG_SUPER_PASSWORD"

  mkdir -p "$PGDATA"
  chmod 700 "$PGDATA"

  if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "[entrypoint] initializing PostgreSQL data directory"
    # scram-sha-256 on both local and TCP connections: the old --auth=trust
    # let any process in the container connect as any role with no password.
    (umask 077; printf '%s\n' "$PG_SUPER_PASSWORD" > /tmp/pg-pwfile)
    "${PG_BIN}/initdb" -D "${PGDATA}" -U postgres --pwfile=/tmp/pg-pwfile \
      --auth-local=scram-sha-256 --auth-host=scram-sha-256 \
      > /tmp/initdb.log 2>&1 \
      || { echo "[entrypoint] initdb failed:"; cat /tmp/initdb.log; rm -f /tmp/pg-pwfile; exit 1; }
    rm -f /tmp/pg-pwfile
  fi

  echo "[entrypoint] starting PostgreSQL"
  "${PG_BIN}/pg_ctl" -D "${PGDATA}" -l /tmp/postgres.log -w \
    -o "-c listen_addresses=localhost -c unix_socket_directories=${PG_SOCKET_DIR}" start
  PG_STARTED=1

  for i in $(seq 1 30); do
    "${PG_BIN}/pg_isready" -q -h "${PG_SOCKET_DIR}" && break
    sleep 1
  done

  pg_sql() { "${PG_BIN}/psql" -h "${PG_SOCKET_DIR}" -U postgres -v ON_ERROR_STOP=1 "$@"; }

  pg_sql -tAc "SELECT 1 FROM pg_roles WHERE rolname='autosys'" | grep -q 1 \
    || pg_sql -qc "CREATE ROLE autosys LOGIN"
  # Re-applied on every start so the role always matches the current
  # password (a changed AUTOSYS_DB_PASSWORD, or a volume from before
  # passwords were generated). Passed as a psql variable, quoted by psql
  # itself (:'pw'), rather than spliced into the SQL string.
  pg_sql -q -v pw="$PG_PASSWORD" <<'SQL'
ALTER ROLE autosys WITH LOGIN PASSWORD :'pw';
SQL
  pg_sql -tAc "SELECT 1 FROM pg_database WHERE datname='autosys'" | grep -q 1 \
    || "${PG_BIN}/createdb" -h "${PG_SOCKET_DIR}" -U postgres -O autosys autosys
  unset PGPASSWORD

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
