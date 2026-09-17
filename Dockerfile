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

COPY --from=frontend-build /app/wcc-frontend/dist ./wcc-frontend/dist
COPY --from=frp-fetch /usr/local/bin/frpc /usr/local/bin/frpc
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

RUN useradd --create-home --uid 1000 autosys \
    && mkdir -p /app/data \
    && chown -R autosys:autosys /app
USER autosys

ENV AUTOSYS_DB_URL=sqlite:///data/autosys.db
VOLUME ["/app/data"]

EXPOSE 9000 8080

# Wraps rather than replaces the command: `docker run <image> autosys scheduler
# serve ...` keeps working unchanged, with the tunnel starting first when
# FRP_SERVER/FRP_TOKEN are set.
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
