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
RUN pip install --no-cache-dir -e .

# nginx is the single origin the browser talks to: WCC's own SPA fallback
# 404s anything under /api/v1/ (login, JIL import), which lives in the API
# app instead, so serving the frontend from WCC's own process would break
# login the moment a browser (not curl hitting a port directly) loads it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx \
    && rm -rf /var/lib/apt/lists/*

COPY --from=frontend-build /app/wcc-frontend/dist ./wcc-frontend/dist
COPY --from=frontend-build /app/wcc-frontend/dist /usr/share/nginx/html
COPY --from=frp-fetch /usr/local/bin/frpc /usr/local/bin/frpc
COPY nginx.bundled.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /app/data

ENV AUTOSYS_DB_URL=sqlite:///data/autosys.db
VOLUME ["/app/data"]

EXPOSE 9000 8080

# With no command: starts the API, WCC backend and nginx together (the
# client-facing mode). A command IS still honoured as before -- e.g. the
# docker-compose*.yml files in this repo run `serve` and `wcc` as separate
# containers -- so that multi-container layout keeps working unchanged.
# Either way the frp tunnel (only ever for port 9000) starts first when
# FRP_SERVER/FRP_TOKEN/FRP_STCP_KEY are set.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

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

COPY --from=frontend-build /app/wcc-frontend/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
