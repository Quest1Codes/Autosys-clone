# PostgreSQL 17 on UBI 10, running as the unprivileged `postgres` user.
#
# Red Hat's public registries carry no PostgreSQL server image for UBI 10 and the
# UBI repos carry only the client, so the server is built from the checksum-pinned
# source tarball -- the same stage the main Dockerfile uses for its bundled-mode
# database, copied here so this directory stays a self-contained build context
# (CI and dependabot build it as `deploy/postgres`). Keep PG_VERSION/PG_SHA256 in
# step with the main Dockerfile.
#
# Behaves like the stock image for the variables the compose files use:
# POSTGRES_USER (default postgres), POSTGRES_PASSWORD (required),
# POSTGRES_DB (default: the user name). Data lives in /var/lib/postgresql/data.
#
# NOTE: a data volume created by the old postgres:16-alpine image cannot be
# reused (different major version, UID and musl -> glibc collation). Dump with
# `pg_dump -Fc` and restore with `pg_restore` into the new volume.

FROM registry.access.redhat.com/ubi10/ubi:10.2 AS pgbuild
# pipefail: a failing first command in a pipe (curl | sha256sum, rpm -ql | while) must fail the build
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ARG PG_VERSION=17.11
ARG PG_SHA256=dd27f2b3c59e73ed14aa3324901242bf69a032a6347805f274e6260322d42979
# bison and flex are build-time only (this stage never reaches the image). UBI's
# repos don't carry them and PostgreSQL's release tarball does not ship the
# generated parser files, so they are built from checksum-pinned tarballs.
ARG BISON_SHA256=9bba0214ccf7f1079c5d59210045227bcf619519840ebfa80cd3849cff5a5bf2
ARG FLEX_SHA256=e87aae032bf07c26f85ac0ed3250998c37621d95f8bd748b31f15b33c45ee995
RUN dnf install -y --setopt=install_weak_deps=false gcc gcc-c++ make tar bzip2 xz \
        openssl-devel zlib-devel diffutils findutils perl perl-FindBin perl-File-Compare perl-File-Copy perl-File-Basename perl-Getopt-Long perl-Data-Dumper perl-Time-HiRes perl-IPC-Cmd m4 \
    && dnf clean all \
    && curl -fsSL -o /tmp/bison.tar.xz https://ftp.gnu.org/gnu/bison/bison-3.8.2.tar.xz \
    && echo "${BISON_SHA256}  /tmp/bison.tar.xz" | sha256sum -c - \
    && tar -C /tmp -xJf /tmp/bison.tar.xz \
    && curl -fsSL -o /tmp/flex.tar.gz https://github.com/westes/flex/releases/download/v2.6.4/flex-2.6.4.tar.gz \
    && echo "${FLEX_SHA256}  /tmp/flex.tar.gz" | sha256sum -c - \
    && tar -C /tmp -xzf /tmp/flex.tar.gz
WORKDIR /tmp/bison-3.8.2
RUN ./configure --prefix=/usr/local >/dev/null && make -j"$(nproc)" >/dev/null && make install >/dev/null
WORKDIR /tmp/flex-2.6.4
RUN ./configure --prefix=/usr/local >/dev/null && make -j"$(nproc)" >/dev/null && make install >/dev/null
RUN curl -fsSL -o /tmp/pg.tar.bz2 "https://ftp.postgresql.org/pub/source/v${PG_VERSION}/postgresql-${PG_VERSION}.tar.bz2" \
    && echo "${PG_SHA256}  /tmp/pg.tar.bz2" | sha256sum -c - \
    && tar -C /tmp -xjf /tmp/pg.tar.bz2
WORKDIR /tmp/postgresql-${PG_VERSION}
RUN ./configure --prefix=/opt/pgsql --with-openssl --without-icu --without-readline \
         --without-libxml --without-libxslt --without-gssapi --without-ldap \
         --without-pam --without-systemd --without-llvm --without-lz4 --without-zstd \
    && make -j"$(nproc)" world-bin \
    && make install-world-bin \
    && (find /opt/pgsql -type f \( -name '*.so*' -o -perm -u+x \) -exec strip --strip-unneeded {} + 2>/dev/null || true) \
    && rm -rf /opt/pgsql/include /opt/pgsql/lib/pkgconfig /opt/pgsql/share/doc /opt/pgsql/share/man \
    && test -x /opt/pgsql/bin/initdb

FROM registry.access.redhat.com/ubi10/ubi:10.2 AS rootfs
# pipefail: a failing first command in a pipe (curl | sha256sum, rpm -ql | while) must fail the build
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN mkdir -p /mnt/rootfs && rpm --root /mnt/rootfs --initdb \
    && dnf install -y --installroot /mnt/rootfs --releasever 10 \
         --setopt=install_weak_deps=false --setopt=tsflags=nodocs \
         bash coreutils-single openssl-libs zlib libstdc++ grep \
    && dnf --installroot /mnt/rootfs clean all \
    && dnf clean all \
    && rm -rf /mnt/rootfs/var/cache/* /mnt/rootfs/var/log/*
COPY --from=pgbuild /opt/pgsql /mnt/rootfs/opt/pgsql
RUN groupadd --root /mnt/rootfs -g 26 postgres \
    && useradd --root /mnt/rootfs -u 26 -g postgres -d /var/lib/postgresql -M -s /sbin/nologin postgres \
    && mkdir -p /mnt/rootfs/var/lib/postgresql/data \
    && chown -R 26:26 /mnt/rootfs/var/lib/postgresql \
    && chmod 700 /mnt/rootfs/var/lib/postgresql/data

FROM registry.access.redhat.com/ubi10/ubi-micro:10.2 AS runtime
COPY --from=rootfs /mnt/rootfs/ /
ENV LANG=C.UTF-8 \
    PGDATA=/var/lib/postgresql/data \
    PATH=/opt/pgsql/bin:/usr/local/bin:/usr/bin:/usr/sbin
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh
VOLUME ["/var/lib/postgresql/data"]
USER 26:26
EXPOSE 5432
HEALTHCHECK --interval=10s --timeout=5s --retries=10 CMD ["/bin/bash", "-c", "pg_isready -U \"${POSTGRES_USER:-postgres}\""]
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["postgres"]
