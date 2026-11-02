#!/bin/bash
# Minimal replacement for the stock postgres image's entrypoint: initialise the
# data directory on first start (scram-sha-256 everywhere, no trust auth), create
# the requested role/database, then run postgres in the foreground.
set -euo pipefail

if [ "${1:-postgres}" != "postgres" ]; then
  exec "$@"
fi

PGUSER_NAME="${POSTGRES_USER:-postgres}"
PGDB_NAME="${POSTGRES_DB:-$PGUSER_NAME}"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  : "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set to initialise the database}"
  mkdir -p "$PGDATA"
  chmod 700 "$PGDATA"
  pwfile="$(mktemp)"
  printf '%s' "$POSTGRES_PASSWORD" > "$pwfile"
  initdb -D "$PGDATA" -U "$PGUSER_NAME" --pwfile="$pwfile" \
    --auth-local=scram-sha-256 --auth-host=scram-sha-256 \
    -E UTF8 --locale=C.UTF-8 > /dev/null
  rm -f "$pwfile"
  # initdb only opens loopback; let other containers on the network connect.
  echo "host all all all scram-sha-256" >> "$PGDATA/pg_hba.conf"

  # Temporary socket-only server to create the database, then stop it.
  pg_ctl -D "$PGDATA" -w -o "-c listen_addresses=''" start > /dev/null
  if [ "$PGDB_NAME" != "postgres" ]; then
    PGPASSWORD="$POSTGRES_PASSWORD" createdb -h /tmp -U "$PGUSER_NAME" "$PGDB_NAME"
  fi
  pg_ctl -D "$PGDATA" -m fast -w stop > /dev/null
fi

exec postgres -D "$PGDATA" -c listen_addresses='*'
