# syntax=docker/dockerfile:1

# DRY_RUN_ONLY=true builds the simulator without any real-execution code
# (agent dispatch/runners/server + unused cross-instance/cloud clients). WCC,
# the UI, nginx, PostgreSQL and the assessment endpoints are unchanged.
#   docker build --target runtime --build-arg DRY_RUN_ONLY=true -t autosys-clone:dryrun .

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
#
# Base is ubi-micro, which has no package manager, so everything is installed
# into /mnt/rootfs from a full UBI 9 stage (dnf --installroot) and the result
# is copied onto ubi-micro. The nginx module stream is enabled on the build
# host AND copied into the install root, otherwise --installroot ignores it and
# installs the stock 1.20.
#
# Python and PostgreSQL are NOT installed from RPMs. Red Hat's python3.12 RPM
# and the PostgreSQL server (UBI 9 only carries the client; the server is in
# PGDG) drag in libraries -- libxml2, krb5, openldap, cyrus-sasl -- that the
# app never uses but that scanners report against. Instead:
#   * Python is an upstream python-build-standalone build (via uv), which is
#     patched on upstream's schedule, not Red Hat's, and carries no system
#     library dependencies beyond glibc.
#   * PostgreSQL 17 is built from the source tarball (checksum-pinned) with
#     libxml, gssapi, ldap, pam, icu and readline switched off, so none of
#     those libraries are installed at all. Same major version as before, so
#     existing /app/data volumes still open.
FROM registry.access.redhat.com/ubi9/ubi AS pgbuild
ARG PG_VERSION=17.11
ARG PG_SHA256=dd27f2b3c59e73ed14aa3324901242bf69a032a6347805f274e6260322d42979
# bison and flex are build-time only (this stage never reaches the image). UBI's
# repos don't carry them and PostgreSQL's release tarball does not ship the
# generated parser files, so they are built from checksum-pinned tarballs.
ARG BISON_SHA256=9bba0214ccf7f1079c5d59210045227bcf619519840ebfa80cd3849cff5a5bf2
ARG FLEX_SHA256=e87aae032bf07c26f85ac0ed3250998c37621d95f8bd748b31f15b33c45ee995
RUN dnf install -y --setopt=install_weak_deps=false gcc gcc-c++ make tar bzip2 xz \
        openssl-devel zlib-devel diffutils findutils perl perl-FindBin perl-File-Compare perl-File-Copy perl-File-Basename perl-Getopt-Long perl-Data-Dumper perl-Time-HiRes perl-IPC-Cmd m4 \
    && curl -fsSL -o /tmp/bison.tar.xz https://ftp.gnu.org/gnu/bison/bison-3.8.2.tar.xz \
    && echo "${BISON_SHA256}  /tmp/bison.tar.xz" | sha256sum -c - \
    && tar -C /tmp -xJf /tmp/bison.tar.xz && cd /tmp/bison-3.8.2 \
    && ./configure --prefix=/usr/local >/dev/null && make -j"$(nproc)" >/dev/null && make install >/dev/null \
    && curl -fsSL -o /tmp/flex.tar.gz https://github.com/westes/flex/releases/download/v2.6.4/flex-2.6.4.tar.gz \
    && echo "${FLEX_SHA256}  /tmp/flex.tar.gz" | sha256sum -c - \
    && tar -C /tmp -xzf /tmp/flex.tar.gz && cd /tmp/flex-2.6.4 \
    && ./configure --prefix=/usr/local >/dev/null && make -j"$(nproc)" >/dev/null && make install >/dev/null
RUN curl -fsSL -o /tmp/pg.tar.bz2 "https://ftp.postgresql.org/pub/source/v${PG_VERSION}/postgresql-${PG_VERSION}.tar.bz2" \
    && echo "${PG_SHA256}  /tmp/pg.tar.bz2" | sha256sum -c - \
    && tar -C /tmp -xjf /tmp/pg.tar.bz2 \
    && cd "/tmp/postgresql-${PG_VERSION}" \
    && ./configure --prefix=/opt/pgsql --with-openssl --without-icu --without-readline \
         --without-libxml --without-libxslt --without-gssapi --without-ldap \
         --without-pam --without-systemd --without-llvm --without-lz4 --without-zstd \
    && make -j"$(nproc)" world-bin \
    && make install-world-bin \
    && (find /opt/pgsql -type f \( -name '*.so*' -o -perm -u+x \) -exec strip --strip-unneeded {} + 2>/dev/null || true) \
    && rm -rf /opt/pgsql/include /opt/pgsql/lib/pkgconfig /opt/pgsql/share/doc /opt/pgsql/share/man \
    && test -x /opt/pgsql/bin/initdb

FROM registry.access.redhat.com/ubi9/ubi AS pybuild
ARG UV_VERSION=0.11.27
ARG PY_VERSION=3.12
RUN dnf install -y --setopt=install_weak_deps=false python3-pip \
    && pip3 install --no-cache-dir "uv==${UV_VERSION}" \
    && UV_PYTHON_INSTALL_DIR=/opt/uvpy uv python install "${PY_VERSION}" \
    && mv /opt/uvpy/cpython-"${PY_VERSION}".* /opt/python \
    # uv marks its interpreters externally managed; pip must install into this one.
    && find /opt/python -name EXTERNALLY-MANAGED -delete \
    && /opt/python/bin/python3 --version

FROM registry.access.redhat.com/ubi9/ubi AS rootfs

RUN mkdir -p /mnt/rootfs && rpm --root /mnt/rootfs --initdb \
    && dnf -y module enable nginx:1.26 \
    && mkdir -p /mnt/rootfs/etc/dnf/modules.d \
    && cp /etc/dnf/modules.d/nginx.module /mnt/rootfs/etc/dnf/modules.d/ \
    && dnf install -y --installroot /mnt/rootfs --releasever 9 \
         --setopt=install_weak_deps=false --setopt=tsflags=nodocs \
         bash coreutils-single nginx openssl-libs zlib libstdc++ grep findutils \
    && dnf --installroot /mnt/rootfs clean all \
    && rm -rf /mnt/rootfs/var/cache/* /mnt/rootfs/var/log/*
COPY --from=pybuild /opt/python /mnt/rootfs/opt/python
COPY --from=pgbuild /opt/pgsql /mnt/rootfs/opt/pgsql
# Fixed numeric UID/GID (see the non-root note further down).
RUN groupadd --root /mnt/rootfs -g 1001 autosys \
    && useradd --root /mnt/rootfs -u 1001 -g autosys -d /app -M -s /sbin/nologin autosys

ARG DRY_RUN_ONLY=false

# autosys/wcc/app.py and autosys/db/connection.py both resolve paths
# relative to the installed package location (Path(__file__).parents[2]),
# expecting autosys/ and wcc-frontend/ to be sibling directories under the
# project root. An editable install keeps that layout intact — do not
# switch this to installing a built wheel.
COPY pyproject.toml README.md /mnt/rootfs/app/
COPY autosys /mnt/rootfs/app/autosys
# The __pycache__ sweep is deliberate, not tidying: .dockerignore keeps
# compiled bytecode out of the build context, but if that entry is ever
# dropped, deleting only the .py files would leave each stripped module's
# .pyc behind -- still readable (`grep shell=True` finds it), which defeats
# the point of the flag. Done unconditionally so both variants are identical
# in this respect.
RUN cd /mnt/rootfs/app && find autosys -name '__pycache__' -type d -prune -exec rm -rf '{}' + ; \
    if [ "$DRY_RUN_ONLY" = "true" ]; then \
      rm -f autosys/agent/dispatch.py autosys/agent/runner.py \
            autosys/agent/runners.py autosys/agent/server.py \
            autosys/engine/xinst_client.py autosys/engine/cloud_integration.py \
            autosys/engine/agent_monitor.py; \
    fi
# The base image bundles its own outdated (CVE'd) pip and setuptools, both
# installed and as ensurepip bootstrap wheels -- upgrade and drop the cached
# wheels so neither carries forward. setuptools vendors its own copies of
# jaraco.context/wheel internally (setuptools/_vendor/...), which is why
# upgrading setuptools alone clears those two as well.
# Run through chroot into the install root (with resolv.conf borrowed for the
# network) so the rpm database can be edited afterwards: see the rpm -e below.
RUN cp /etc/resolv.conf /mnt/rootfs/etc/resolv.conf \
    && chroot /mnt/rootfs sh -c 'cd /app && /opt/python/bin/python3 -m pip install --no-cache-dir --upgrade pip setuptools'
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
RUN chroot /mnt/rootfs sh -c 'cd /app \
      && /opt/python/bin/python3 -m pip install --no-cache-dir -e .[pg] \
      && /opt/python/bin/python3 -m pip install --no-cache-dir --upgrade setuptools \
      && /opt/python/bin/python3 -m pip uninstall -y pip' \
    # pip bundles its own (older) urllib3/msgpack/setuptools copies under
    # pip/_vendor, which scanners report as CVEs in this image. Nothing at
    # runtime installs packages, so drop pip once the install is done -- and
    # (there is no rpm pip any more: Python is not an RPM).
    && rm -f /mnt/rootfs/etc/resolv.conf
# Packages nothing in the container uses at runtime. dnf pulls them in as
# dependencies of nginx (systemd, dbus, pam), of useradd (shadow-utils) and of
# the install scriptlets (sed); the container never runs systemd, dbus, PAM
# logins or package scripts. The `openssl` command-line tool (only PAM needed
# it) goes too; the libraries (openssl-libs) stay for nginx and PostgreSQL. Erased with --nodeps from the rpm database too, so
# scanners stop reporting CVEs against files that are gone. The ldd check right
# after fails the build if any ELF binary we ship now lacks a shared library.
RUN for p in systemd systemd-pam systemd-libs systemd-rpm-macros dbus dbus-broker dbus-common \
             util-linux util-linux-core libfdisk libsmartcols shadow-utils libsemanage \
             kmod-libs sed pam libpwquality cracklib cracklib-dicts audit-libs libdb \
             libutempter libseccomp expat libgcrypt libgpg-error lz4-libs libzstd \
             libcap-ng libeconf xz-libs redhat-logos-httpd openssl \
             acl gzip bzip2-libs libmount libblkid libuuid \
             p11-kit p11-kit-trust libtasn1 libffi alternatives \
             openssl-fips-provider openssl-fips-provider-so; do \
      if rpm --root /mnt/rootfs -q "$p" >/dev/null 2>&1; then \
        rpm --root /mnt/rootfs -ql "$p" | while read -r f; do \
          if [ -f "/mnt/rootfs$f" ] || [ -L "/mnt/rootfs$f" ]; then echo "$f"; fi; done >> /mnt/rootfs/.removed-files; \
        rpm --root /mnt/rootfs -e --nodeps "$p"; \
      fi; \
    done \
    # _tkinter (needs Tcl/Tk) and _crypt (needs libxcrypt-compat) are unused by
    # the app and would otherwise leave unresolved libraries in the image.
    && find /mnt/rootfs/opt/python -name '_tkinter*.so' -delete -o -name '_crypt*.so' -delete \
    && chroot /mnt/rootfs bash -c ' \
         bad=0; \
         for f in /usr/sbin/nginx /usr/bin/bash /usr/bin/grep /usr/bin/find \
                  $(find /opt/python /opt/pgsql -type f \( -name "*.so*" -o -perm -u+x \)); do \
           if ldd "$f" 2>/dev/null | grep -q "not found"; then echo "missing library for $f"; bad=1; fi; \
         done; exit $bad'
# After the library check: ncurses-base is terminfo data and pcre2-syntax is
# documentation; both are data nothing reads at runtime.
# glibc-common and glibc-minimal-langpack STAY: glibc-common owns the C.utf8
# locale files. Without that locale (and without LANG) initdb silently creates
# a SQL_ASCII database, and every non-ASCII character in a JIL file then fails
# to insert. Erasing the package but keeping the files would leave files no
# package owns, which scanners cannot see.
RUN for p in ncurses-base pcre2-syntax; do \
      rpm --root /mnt/rootfs -ql "$p" | while read -r f; do \
        if [ -f "/mnt/rootfs$f" ] || [ -L "/mnt/rootfs$f" ]; then echo "$f"; fi; done >> /mnt/rootfs/.removed-files; \
      rpm --root /mnt/rootfs -e --nodeps "$p"; \
    done

FROM registry.access.redhat.com/ubi9/ubi-micro AS runtime
COPY --from=rootfs /mnt/rootfs/ /
# The python base image this replaced set the same; initdb, psql and Python
# derive their encoding from it.
ENV LANG=C.UTF-8
# ubi-micro's own layer already contains files of some packages erased above
# (glibc-common's ldd/getent/iconv, locales, terminfo). Erasing them from the
# rpm database does not delete them from that layer, which would leave files
# on disk that no package owns and scanners can no longer see. Delete every
# file recorded for an erased package.
RUN while read -r f; do rm -f -- "$f"; done < /.removed-files && rm -f /.removed-files
ENV PATH=/opt/python/bin:/usr/local/bin:/usr/bin:/usr/sbin
WORKDIR /app

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
# nginx, bash, postgresql17-server and python were installed into the root
# filesystem by the rootfs stage above; nothing to install here.

# Non-root runtime (static-analysis finding SEC-01: this image used to run
# every process -- API, WCC, nginx, PostgreSQL -- as UID 0, which bank
# container policies reject outright). Everything now runs as one fixed,
# numeric UID so `runAsNonRoot` style admission checks can verify it without
# resolving a name. Single user rather than per-process users: the old
# design only needed root to `su postgres`, and PostgreSQL is equally happy
# to run as any non-root user that owns its data directory.
RUN rm -f /etc/nginx/conf.d/*.conf \
    && mkdir -p /var/lib/nginx/tmp /var/lib/nginx/logs \
    && chown -R autosys:autosys /var/lib/nginx
# Replaces RHEL's nginx.conf wholesale: its stock file declares a default
# server on :80 (privileged, unused), a `user` directive and root-owned pid
# and log paths, each of which fails or warns unprivileged. Logs go to
# stdout/stderr for the cluster's log collector; temp dirs and the pid file
# are writable by the runtime user.
RUN printf '%s\n' \
      'pid /tmp/nginx.pid;' \
      'error_log /dev/stderr warn;' \
      'events { worker_connections 1024; }' \
      'http {' \
      '    include /etc/nginx/mime.types;' \
      '    default_type application/octet-stream;' \
      '    log_format main "$remote_addr - $remote_user [$time_local] \"$request\" $status $body_bytes_sent \"$http_referer\" \"$http_user_agent\"";' \
      '    access_log /dev/stdout main;' \
      '    client_body_temp_path /var/lib/nginx/tmp/client_body;' \
      '    proxy_temp_path /var/lib/nginx/tmp/proxy;' \
      '    fastcgi_temp_path /var/lib/nginx/tmp/fastcgi;' \
      '    uwsgi_temp_path /var/lib/nginx/tmp/uwsgi;' \
      '    scgi_temp_path /var/lib/nginx/tmp/scgi;' \
      '    include /etc/nginx/conf.d/*.conf;' \
      '}' > /etc/nginx/nginx.conf

COPY --from=frontend-build /app/wcc-frontend/dist ./wcc-frontend/dist
COPY --from=frontend-build /app/wcc-frontend/dist /usr/share/nginx/html
COPY nginx.bundled.conf /etc/nginx/conf.d/default.conf
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
FROM nginx:1.31-alpine AS nginx-runtime

# Same reasoning as the runtime stage's apt-get upgrade: apply whatever
# Alpine has already patched since this tag's snapshot was published.
RUN apk update && apk upgrade && rm -rf /var/cache/apk/*

COPY --from=frontend-build /app/wcc-frontend/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
# Static index only, so it doesn't depend on the api/wcc upstreams being up.
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD wget -q --spider http://127.0.0.1/ || exit 1
