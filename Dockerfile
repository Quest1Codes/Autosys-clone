# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 — build the WCC React frontend
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend-build

WORKDIR /app/wcc-frontend

COPY wcc-frontend/package.json wcc-frontend/package-lock.json ./
RUN npm ci

COPY wcc-frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 1b — fetch the frp client binary
# ---------------------------------------------------------------------------
# frpc lets a simulator running on a client machine (behind NAT, no public IP)
# be reached by Shinro: it dials OUT to a relay we run, so the client never has
# to open an inbound port. Fetched in its own stage so the download tooling and
# tarball don't end up as layers in the runtime image.
FROM alpine:3.20 AS frp-fetch

ARG FRP_VERSION=0.71.0
# Set automatically by buildx; defaulted so a plain `docker build` still works.
ARG TARGETARCH=amd64

RUN apk add --no-cache curl tar \
    && curl -fsSL -o /tmp/frp.tar.gz \
        "https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_${TARGETARCH}.tar.gz" \
    && tar -xzf /tmp/frp.tar.gz -C /tmp \
    && mv "/tmp/frp_${FRP_VERSION}_linux_${TARGETARCH}/frpc" /usr/local/bin/frpc \
    && chmod 755 /usr/local/bin/frpc

# ---------------------------------------------------------------------------
# Stage 2 — runtime image
# ---------------------------------------------------------------------------
# This is the ONE image a client runs. It bundles all three pieces a real
# engagement needs: the assessment API + EPS (port 9000, what Shinro tunnels
# to), the WCC dashboard backend (internal-only), and nginx serving the WCC
# frontend at :8080 so the client can log in and load their own JIL files --
# a distribution split across separate containers/images would need the
# client to run docker-compose and know which piece is which, which defeats
# the point of a one-line `docker run`.
FROM python:3.11-slim AS runtime

WORKDIR /app

# autosys/wcc/app.py and autosys/db/connection.py both resolve paths
# relative to the installed package location (Path(__file__).parents[2]),
# expecting autosys/ and wcc-frontend/ to be sibling directories under the
# project root. An editable install keeps that layout intact — do not
# switch this to installing a built wheel.
COPY pyproject.toml README.md ./
COPY autosys ./autosys
# The base image bundles its own outdated (CVE'd) pip and setuptools, both
# installed and as ensurepip bootstrap wheels -- upgrade and drop the cached
# wheels so neither carries forward. setuptools vendors its own copies of
# jaraco.context/wheel internally (setuptools/_vendor/...), which is why
# upgrading setuptools alone clears those two as well.
RUN pip install --no-cache-dir --upgrade pip setuptools && \
    rm -f /usr/local/lib/python3.11/ensurepip/_bundled/pip-*.whl \
          /usr/local/lib/python3.11/ensurepip/_bundled/setuptools-*.whl
# [pg] pulls in psycopg2-binary/asyncpg — the compose files point
# AUTOSYS_DB_URL at a PostgreSQL container, not the SQLite default.
RUN pip install --no-cache-dir -e .[pg]

# nginx is the single origin the browser talks to: WCC's own SPA fallback
# 404s anything under /api/v1/ (login, JIL import), which lives in the API
# app instead, so serving the frontend from WCC's own process would break
# login the moment a browser (not curl hitting a port directly) loads it.
#
# postgresql (server + client binaries) backs the client-facing bundle mode
# (docker-entrypoint.sh starts and initializes it under /app/data/pgdata) --
# there is no separate DB container to pair with a one-line `docker run`,
# and SQLite's single-writer model cannot take 300k+ files' worth of
# concurrent writes. Only that mode uses it; the multi-container compose
# files run their own separate `postgres` service instead.
# upgrade first: the base image's own published snapshot lags Debian's
# security repo by however long it's been since that snapshot was built,
# same as any floating tag -- apply what's already patched upstream before
# installing anything else, so nginx/postgresql land on top of current
# packages rather than whatever shipped with the base image.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends nginx postgresql \
    && rm -rf /var/lib/apt/lists/*

COPY --from=frontend-build /app/wcc-frontend/dist ./wcc-frontend/dist
COPY --from=frontend-build /app/wcc-frontend/dist /usr/share/nginx/html
COPY --from=frp-fetch /usr/local/bin/frpc /usr/local/bin/frpc
COPY nginx.bundled.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /app/data

# Overridden by docker-entrypoint.sh once the bundled PostgreSQL is up (client-
# facing bundle mode); the multi-container compose files override this
# themselves to point at their own `postgres` service. This default only
# matters if something reads it before the entrypoint runs.
ENV AUTOSYS_DB_URL=postgresql+psycopg2://autosys:autosys@localhost:5432/autosys
VOLUME ["/app/data"]

EXPOSE 9000 8080

# With no command: starts the API, WCC backend and nginx together (the
# client-facing mode). A command IS still honoured as before -- e.g. the
# docker-compose*.yml files in this repo run `serve` and `wcc` as separate
# containers -- so that multi-container layout keeps working unchanged.
# Either way the frp tunnel (only ever for port 9000) starts first when
# FRP_SERVER/FRP_TOKEN/FRP_STCP_KEY are set.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# CMD must be explicitly empty: python:3.11-slim's own default is
# `CMD ["python3"]`, which this stage never overrode -- so a plain
# `docker run <image>` (no trailing command) was actually passing "python3"
# as the entrypoint's "$@", silently skipping the "no command -> bundle
# mode" branch in docker-entrypoint.sh and falling through to `exec python3`,
# which exits(0) immediately on EOF from stdin. Confirmed in testing: only
# the frp-tunnel-enabled log line appeared, then the container exited clean.
CMD []

# ---------------------------------------------------------------------------
# Stage 3 — nginx front door
# ---------------------------------------------------------------------------
# The built frontend is served here rather than by `autosys scheduler wcc`
# (port 8080) so it can share one origin with the api service (port 9000):
# the wcc app's own SPA fallback route 404s anything under /api/v1/, which
# is where auth/JIL-import/sendevent live, so serving the SPA from its own
# process would break login the moment a real browser (not curl hitting a
# port directly) loads it.
FROM nginx:1.27-alpine AS nginx-runtime

# Same reasoning as the runtime stage's apt-get upgrade: apply whatever
# Alpine has already patched since this tag's snapshot was published.
RUN apk update && apk upgrade && rm -rf /var/cache/apk/*

COPY --from=frontend-build /app/wcc-frontend/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
