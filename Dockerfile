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
# Stage 2 — runtime image
# ---------------------------------------------------------------------------
# This is the ONE image a client runs. It bundles all three pieces a real
# engagement needs: the assessment API + EPS (port 9000, what Shinro tunnels
# to), the WCC dashboard backend (internal-only), and nginx serving the WCC
# frontend at :8080 so the client can log in and load their own JIL files --
# a distribution split across separate containers/images would need the
# client to run docker-compose and know which piece is which, which defeats
# the point of a one-line `docker run`.
FROM python:3.14-alpine AS runtime

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
#
# The second setuptools upgrade, after -e ., is deliberate and not redundant:
# `pip install -e .` resolves its own PEP 517 build-isolation environment
# against [build-system] requires ("setuptools >= 68.0"), which is satisfied
# by -- and so pip is happy to leave in place -- whatever older setuptools
# was already cached, undoing the upgrade above. Verified via Trivy: without
# this second line, the shipped image still carried setuptools 70.3.0 with a
# known CVE despite the upgrade three lines up.
RUN pip install --no-cache-dir -e .[pg] \
    && pip install --no-cache-dir --upgrade setuptools \
    # pip bundles its own (older) urllib3/msgpack/setuptools copies under
    # pip/_vendor, which scanners report as CVEs in this image. Nothing at
    # runtime installs packages, so drop pip once the install is done.
    && python -m pip uninstall -y pip

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
# Alpine (musl) rather than Debian slim: the Debian images carry ~270 unpatched
# OS-package CVEs (glibc, libxml2, systemd libs...) with no fix available, where
# Alpine's far smaller package set has none outstanding. bash is needed by
# docker-entrypoint.sh (`wait -n`, arrays).
#
# PostgreSQL 17, the same major version the Debian image shipped: PostgreSQL
# cannot open a data directory written by a newer major version, so moving to
# Alpine's postgresql16 made every existing /app/data volume fail to start.
#
# upgrade first: the base image's own published snapshot lags Alpine's
# security repo by however long it's been since that snapshot was built --
# apply what's already patched upstream before installing anything else.
RUN apk upgrade --no-cache \
    && apk add --no-cache bash nginx postgresql17

# Non-root runtime (static-analysis finding SEC-01: this image used to run
# every process -- API, WCC, nginx, PostgreSQL -- as UID 0, which bank
# container policies reject outright). Everything now runs as one fixed,
# numeric UID so `runAsNonRoot` style admission checks can verify it without
# resolving a name. Single user rather than per-process users: the old
# design only needed root to `su postgres`, and PostgreSQL is equally happy
# to run as any non-root user that owns its data directory.
#
# nginx needs adjustments to run unprivileged: Alpine's stock default site
# (http.d/default.conf) listens on :80 -- a privileged port, and unused, ours
# is http.d/default.conf on :8080 -- its pid file defaults to root-owned /run, and
# the `user` directive only applies to a root master process (a warning
# otherwise). Its log and temp dirs are handed to the runtime user.
RUN addgroup -S -g 1001 autosys \
    && adduser -S -u 1001 -G autosys -h /app -H -s /sbin/nologin autosys \
    && rm -f /etc/nginx/http.d/default.conf \
    && sed -i -e '/^user /d' -e '1i pid /tmp/nginx.pid;' /etc/nginx/nginx.conf \
    && mkdir -p /var/lib/nginx/tmp /var/log/nginx \
    && chown -R autosys:autosys /var/lib/nginx /var/log/nginx

COPY --from=frontend-build /app/wcc-frontend/dist ./wcc-frontend/dist
COPY --from=frontend-build /app/wcc-frontend/dist /usr/share/nginx/html
COPY nginx.bundled.conf /etc/nginx/http.d/default.conf
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

# /app/data is the only path the app writes to (SQLite default, bundled
# PostgreSQL's data dir, generated DB credentials). Owned by the runtime
# user here so a fresh named volume inherits that ownership on first mount.
RUN mkdir -p /app/data && chown autosys:autosys /app/data

# No AUTOSYS_DB_URL default here on purpose. There used to be one with an
# `autosys:autosys` password baked in (static-analysis finding SEC-06, flagged
# as a hardcoded credential). Nothing needs it: bundle mode's entrypoint
# exports its own URL with a generated password, and every compose file sets
# its own. Unset, the app falls back to SQLite under /app/data.
VOLUME ["/app/data"]

USER 1001:1001

EXPOSE 9000 8080

# With no command: starts the API, WCC backend and nginx together (the
# client-facing mode). A command IS still honoured as before -- e.g. the
# docker-compose*.yml files in this repo run `serve` and `wcc` as separate
# containers -- so that multi-container layout keeps working unchanged.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# CMD must be explicitly empty: the python base image's own default is
# `CMD ["python3"]`, which this stage never overrode -- so a plain
# `docker run <image>` (no trailing command) was actually passing "python3"
# as the entrypoint's "$@", silently skipping the "no command -> bundle
# mode" branch in docker-entrypoint.sh and falling through to `exec python3`,
# which exits(0) immediately on EOF from stdin. Confirmed in testing: only
# the entrypoint's first log line appeared, then the container exited clean.
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
# Static index only, so it doesn't depend on the api/wcc upstreams being up.
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD wget -q --spider http://127.0.0.1/ || exit 1
