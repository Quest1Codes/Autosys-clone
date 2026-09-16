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

RUN useradd --create-home --uid 1000 autosys \
    && mkdir -p /app/data \
    && chown -R autosys:autosys /app
USER autosys

ENV AUTOSYS_DB_URL=sqlite:///data/autosys.db
VOLUME ["/app/data"]

EXPOSE 9000 8080

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
