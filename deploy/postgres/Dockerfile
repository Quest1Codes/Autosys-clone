# postgres:16 on Alpine, running as the unprivileged `postgres` user.
#
# Alpine rather than the Debian default: the Debian image carries ~300
# unpatched OS-package CVEs (glibc, libxml2, systemd libs...) with no fix
# available; the Alpine image has none outstanding.
#
# The stock image starts as root and uses its bundled gosu to drop to `postgres`
# after fixing data-dir ownership. Starting as `postgres` from the beginning
# makes that step unnecessary (the entrypoint skips it when not root), so gosu
# is removed -- it was also built with an old Go toolchain that scanners flag.
# A fresh named volume is initialised with the image's own ownership of
# /var/lib/postgresql/data, so no extra setup is needed.
#
# NOTE: an existing volume created by the Debian image cannot be reused as-is
# (different UID and a glibc -> musl collation change that can corrupt text
# indexes). Dump and restore, or `docker volume rm autosys-pgdata` for dev data.
FROM postgres:16-alpine
RUN rm -f /usr/local/bin/gosu
USER postgres
HEALTHCHECK --interval=10s --timeout=5s --retries=10 CMD pg_isready -U "${POSTGRES_USER:-postgres}" || exit 1
