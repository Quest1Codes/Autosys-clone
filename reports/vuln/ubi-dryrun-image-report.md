# Vulnerability report: autosys-clone dry-run image (UBI 9 micro)

- **Image:** `autosys-clone:dryrun-ubi` (built with `--target runtime --build-arg DRY_RUN_ONLY=true`), 405 MB
- **Scanner:** Trivy 0.74.0, OS packages (`--scanners vuln`), scan 9 on 2026-10-26
- **Base:** `ubi9/ubi-micro`; Python 3.12 from python-build-standalone; PostgreSQL 17.11 built from source; nginx 1.26.3 (UBI module stream)
- **Every finding below is a package with no patched version available from Red Hat at scan time.**

## Summary

| | Critical | High | Medium | Low | Total |
|---|---:|---:|---:|---:|---:|
| All findings reported | 0 | 6 | 48 | 26 | 80 |
| False positive (code absent) | 0 | 0 | 0 | 3 | 3 |
| Not exploitable as configured | 0 | 6 | 32 | 0 | 38 |
| **Open (not ruled out)** | 0 | 0 | 16 | 23 | 39 |

Rows are one per CVE per package, so one CVE can appear on several packages (for example nginx, nginx-core and nginx-filesystem). Distinct CVEs: 44 total, 23 open.

## Scan history

| Scan | Change made before the scan | Critical | High | Medium | Low | Total |
|---|---|---:|---:|---:|---:|---:|
| 1 | Python 3.11, nginx 1.20.1 | 0 | 30 | 147 | 60 | 237 |
| 2 | nginx 1.26.3 | 0 | 27 | 147 | 57 | 231 |
| 3 | Python 3.12 | 0 | 25 | 149 | 53 | 227 |
| 4 | pip rpms removed | 0 | 21 | 137 | 53 | 211 |
| 5 | systemd / util-linux removed | 0 | 13 | 127 | 53 | 193 |
| 6 | Upstream Python, source-built Postgres, pruned packages | 0 | 12 | 66 | 38 | 116 |
| 7 | openssl CLI removed | 0 | 12 | 56 | 28 | 96 |
| 8 | Unused packages removed | 0 | 6 | 49 | 28 | 83 |
| 9 | Unused files removed, UTF-8 locale restored | 0 | 6 | 48 | 26 | 80 |

Scans 1 to 5 predate the move to upstream Python and source-built PostgreSQL. Counts are raw (ruled-out findings included).

## Ruled out

**False positive** means the vulnerable code or program is absent from the image. **Not exploitable as configured** means the code is present but the feature or path is not enabled or reachable here; it could apply if the configuration changes. Proof was gathered by running commands inside the running container on 2026-10-26.

### False positives: code absent (3 rows, 1 CVEs)

| CVE | Severity | Packages | Why | Proof |
|---|---|---|---|---|
| [CVE-2025-53859](https://avd.aquasec.com/nvd/cve-2025-53859) | Low | nginx, nginx-core, nginx-filesystem | The mail/SMTP module is not configured; there is no mail block. | There is no load_module and /usr/lib64/nginx/modules is empty, so the dynamic mail and stream modules are not shipped or loaded. nginx -T (the full effective config) has no ssl, http2, quic/http3, alias, charset, ssi, aio, scgi/uwsgi/fastcgi, mail or stream directives. The only listener is 'listen 8080'. nginx is compiled with ssl, v2 and v3 support, but none of it is configured. |

### Not exploitable as configured (38 rows, 20 CVEs)

| CVE | Severity | Packages | Why | Proof |
|---|---|---|---|---|
| [CVE-2026-15176](https://avd.aquasec.com/nvd/cve-2026-15176) | High | nginx, nginx-core, nginx-filesystem | The affected directive (alias with rewrite) is not used: nginx.bundled.conf has no alias. | nginx -T (the full effective config) has no ssl, http2, quic/http3, alias, charset, ssi, aio, scgi/uwsgi/fastcgi, mail or stream directives. The only listener is 'listen 8080'. nginx is compiled with ssl, v2 and v3 support, but none of it is configured. |
| [CVE-2026-97205](https://avd.aquasec.com/nvd/cve-2026-97205) | High | nginx, nginx-core, nginx-filesystem | Needs aio enabled and SSI pages using include virtual; the config enables neither (fixed upstream in nginx 1.30.x). | nginx -T (the full effective config) has no ssl, http2, quic/http3, alias, charset, ssi, aio, scgi/uwsgi/fastcgi, mail or stream directives. The only listener is 'listen 8080'. nginx is compiled with ssl, v2 and v3 support, but none of it is configured. No aio or ssi directive appears in nginx -T. Debian lists the fix in nginx 1.30.x; UBI 9 ships 1.26.3. |
| [CVE-2026-28755](https://avd.aquasec.com/nvd/cve-2026-28755) | Medium | nginx, nginx-core, nginx-filesystem | OCSP needs TLS; nginx listens on plain :8080 with no ssl settings. | nginx -T (the full effective config) has no ssl, http2, quic/http3, alias, charset, ssi, aio, scgi/uwsgi/fastcgi, mail or stream directives. The only listener is 'listen 8080'. nginx is compiled with ssl, v2 and v3 support, but none of it is configured. |
| [CVE-2026-40460](https://avd.aquasec.com/nvd/cve-2026-40460) | Medium | nginx, nginx-core, nginx-filesystem | HTTP/3 (QUIC) is not enabled; the only listener is plain :8080. | nginx -T (the full effective config) has no ssl, http2, quic/http3, alias, charset, ssi, aio, scgi/uwsgi/fastcgi, mail or stream directives. The only listener is 'listen 8080'. nginx is compiled with ssl, v2 and v3 support, but none of it is configured. |
| [CVE-2026-40701](https://avd.aquasec.com/nvd/cve-2026-40701) | Medium | nginx, nginx-core, nginx-filesystem | ngx_http_ssl_module is not in use; no ssl listener is configured. | nginx -T (the full effective config) has no ssl, http2, quic/http3, alias, charset, ssi, aio, scgi/uwsgi/fastcgi, mail or stream directives. The only listener is 'listen 8080'. nginx is compiled with ssl, v2 and v3 support, but none of it is configured. |
| [CVE-2026-42772](https://avd.aquasec.com/nvd/cve-2026-42772) | Medium | openssl-libs | OpenSSL QUIC is not used: nginx has no http3/quic listener and nothing else in the image speaks QUIC. | System libssl is mapped only by nginx and PostgreSQL (checked /proc/*/maps). nginx has no ssl/quic listener (nginx -T). PostgreSQL: 'show ssl' = off and pg_stat_ssl shows 0 TLS connections. The Python process uses psycopg2's own bundled libssl, not the system one. No private keys on disk (find for *.key, *.p12, *.pfx and grep for PRIVATE KEY found none). Config-dependent: if TLS, QUIC, DTLS, SM2 or CMP is ever enabled, this finding applies. |
| [CVE-2026-42926](https://avd.aquasec.com/nvd/cve-2026-42926) | Medium | nginx, nginx-core, nginx-filesystem | HTTP/2 is not enabled; the config has no http2 listener. | nginx -T (the full effective config) has no ssl, http2, quic/http3, alias, charset, ssi, aio, scgi/uwsgi/fastcgi, mail or stream directives. The only listener is 'listen 8080'. nginx is compiled with ssl, v2 and v3 support, but none of it is configured. |
| [CVE-2026-42934](https://avd.aquasec.com/nvd/cve-2026-42934) | Medium | nginx, nginx-core, nginx-filesystem | No charset directive is configured, so the charset module does nothing. | nginx -T (the full effective config) has no ssl, http2, quic/http3, alias, charset, ssi, aio, scgi/uwsgi/fastcgi, mail or stream directives. The only listener is 'listen 8080'. nginx is compiled with ssl, v2 and v3 support, but none of it is configured. |
| [CVE-2026-42946](https://avd.aquasec.com/nvd/cve-2026-42946) | Medium | nginx, nginx-core, nginx-filesystem | No scgi or uwsgi backend is configured; only proxy_pass to local ports. | nginx -T (the full effective config) has no ssl, http2, quic/http3, alias, charset, ssi, aio, scgi/uwsgi/fastcgi, mail or stream directives. The only listener is 'listen 8080'. nginx is compiled with ssl, v2 and v3 support, but none of it is configured. |
| [CVE-2026-48142](https://avd.aquasec.com/nvd/cve-2026-48142) | Medium | nginx, nginx-core, nginx-filesystem | No charset directive is configured, so the charset module does nothing. | nginx -T (the full effective config) has no ssl, http2, quic/http3, alias, charset, ssi, aio, scgi/uwsgi/fastcgi, mail or stream directives. The only listener is 'listen 8080'. nginx is compiled with ssl, v2 and v3 support, but none of it is configured. |
| [CVE-2026-54872](https://avd.aquasec.com/nvd/cve-2026-54872) | Medium | openssl-libs | Needs an observer timing EC private-key operations; the image terminates no TLS and holds no EC private keys. | System libssl is mapped only by nginx and PostgreSQL (checked /proc/*/maps). nginx has no ssl/quic listener (nginx -T). PostgreSQL: 'show ssl' = off and pg_stat_ssl shows 0 TLS connections. The Python process uses psycopg2's own bundled libssl, not the system one. No private keys on disk (find for *.key, *.p12, *.pfx and grep for PRIVATE KEY found none). Config-dependent: if TLS, QUIC, DTLS, SM2 or CMP is ever enabled, this finding applies. |
| [CVE-2026-54873](https://avd.aquasec.com/nvd/cve-2026-54873) | Medium | openssl-libs | OpenSSL QUIC is not used: nginx has no http3/quic listener and nothing else in the image speaks QUIC. | System libssl is mapped only by nginx and PostgreSQL (checked /proc/*/maps). nginx has no ssl/quic listener (nginx -T). PostgreSQL: 'show ssl' = off and pg_stat_ssl shows 0 TLS connections. The Python process uses psycopg2's own bundled libssl, not the system one. No private keys on disk (find for *.key, *.p12, *.pfx and grep for PRIVATE KEY found none). Config-dependent: if TLS, QUIC, DTLS, SM2 or CMP is ever enabled, this finding applies. |
| [CVE-2026-54875](https://avd.aquasec.com/nvd/cve-2026-54875) | Medium | openssl-libs | SM2 is not used: no SM2 keys or operations anywhere in the image. | System libssl is mapped only by nginx and PostgreSQL (checked /proc/*/maps). nginx has no ssl/quic listener (nginx -T). PostgreSQL: 'show ssl' = off and pg_stat_ssl shows 0 TLS connections. The Python process uses psycopg2's own bundled libssl, not the system one. No private keys on disk (find for *.key, *.p12, *.pfx and grep for PRIVATE KEY found none). Config-dependent: if TLS, QUIC, DTLS, SM2 or CMP is ever enabled, this finding applies. |
| [CVE-2026-56391](https://avd.aquasec.com/nvd/cve-2026-56391) | Medium | coreutils-single | The bug is in the uniq command. Nothing in the image runs uniq: the entrypoint, the Dockerfile and the application code never call it. | grep -w uniq over docker-entrypoint.sh and /app/autosys finds no use. /usr/bin/uniq is still in the image (coreutils-single is one multi-call binary), so this is unused, not absent: anyone who gets a shell could run it. |
| [CVE-2026-72897](https://avd.aquasec.com/nvd/cve-2026-72897) | Medium | openssl-libs | Needs a TLS server that switches contexts on SNI; nginx has no ssl listener and PostgreSQL ssl is off. | System libssl is mapped only by nginx and PostgreSQL (checked /proc/*/maps). nginx has no ssl/quic listener (nginx -T). PostgreSQL: 'show ssl' = off and pg_stat_ssl shows 0 TLS connections. The Python process uses psycopg2's own bundled libssl, not the system one. No private keys on disk (find for *.key, *.p12, *.pfx and grep for PRIVATE KEY found none). Config-dependent: if TLS, QUIC, DTLS, SM2 or CMP is ever enabled, this finding applies. |
| [CVE-2026-75804](https://avd.aquasec.com/nvd/cve-2026-75804) | Medium | openssl-libs | OpenSSL QUIC is not used: nginx has no http3/quic listener and nothing else in the image speaks QUIC. | System libssl is mapped only by nginx and PostgreSQL (checked /proc/*/maps). nginx has no ssl/quic listener (nginx -T). PostgreSQL: 'show ssl' = off and pg_stat_ssl shows 0 TLS connections. The Python process uses psycopg2's own bundled libssl, not the system one. No private keys on disk (find for *.key, *.p12, *.pfx and grep for PRIVATE KEY found none). Config-dependent: if TLS, QUIC, DTLS, SM2 or CMP is ever enabled, this finding applies. |
| [CVE-2026-75805](https://avd.aquasec.com/nvd/cve-2026-75805) | Medium | openssl-libs | CMP (certificate management) is not used: nothing in the image runs a CMP client. | System libssl is mapped only by nginx and PostgreSQL (checked /proc/*/maps). nginx has no ssl/quic listener (nginx -T). PostgreSQL: 'show ssl' = off and pg_stat_ssl shows 0 TLS connections. The Python process uses psycopg2's own bundled libssl, not the system one. No private keys on disk (find for *.key, *.p12, *.pfx and grep for PRIVATE KEY found none). Config-dependent: if TLS, QUIC, DTLS, SM2 or CMP is ever enabled, this finding applies. |
| [CVE-2026-75806](https://avd.aquasec.com/nvd/cve-2026-75806) | Medium | openssl-libs | DTLS is not used: no DTLS listener or client in the image. | System libssl is mapped only by nginx and PostgreSQL (checked /proc/*/maps). nginx has no ssl/quic listener (nginx -T). PostgreSQL: 'show ssl' = off and pg_stat_ssl shows 0 TLS connections. The Python process uses psycopg2's own bundled libssl, not the system one. No private keys on disk (find for *.key, *.p12, *.pfx and grep for PRIVATE KEY found none). Config-dependent: if TLS, QUIC, DTLS, SM2 or CMP is ever enabled, this finding applies. |
| [CVE-2026-77696](https://avd.aquasec.com/nvd/cve-2026-77696) | Medium | openssl-libs | SM2 is not used: no SM2 keys or operations anywhere in the image. | System libssl is mapped only by nginx and PostgreSQL (checked /proc/*/maps). nginx has no ssl/quic listener (nginx -T). PostgreSQL: 'show ssl' = off and pg_stat_ssl shows 0 TLS connections. The Python process uses psycopg2's own bundled libssl, not the system one. No private keys on disk (find for *.key, *.p12, *.pfx and grep for PRIVATE KEY found none). Config-dependent: if TLS, QUIC, DTLS, SM2 or CMP is ever enabled, this finding applies. |
| [CVE-2026-84784](https://avd.aquasec.com/nvd/cve-2026-84784) | Medium | openssl-libs | OpenSSL QUIC is not used: nginx has no http3/quic listener and nothing else in the image speaks QUIC. | System libssl is mapped only by nginx and PostgreSQL (checked /proc/*/maps). nginx has no ssl/quic listener (nginx -T). PostgreSQL: 'show ssl' = off and pg_stat_ssl shows 0 TLS connections. The Python process uses psycopg2's own bundled libssl, not the system one. No private keys on disk (find for *.key, *.p12, *.pfx and grep for PRIVATE KEY found none). Config-dependent: if TLS, QUIC, DTLS, SM2 or CMP is ever enabled, this finding applies. |

## Open findings

| CVE | Severity | Packages | Installed | What it is |
|---|---|---|---|---|
| [CVE-2026-102010](https://avd.aquasec.com/nvd/cve-2026-102010) | Medium | libgcc, libstdc++ | 11.5.0-14.el9 | gcc-toolset-15-gcc: gcc: gcc-toolset-16: gcc: Denial of Service via use-after-free in binary heap erase_if |
| [CVE-2026-8674](https://avd.aquasec.com/nvd/cve-2026-8674) | Medium | glibc, glibc-common, glibc-minimal-langpack | 2.34-283.el9_8 | glibc: glibc DNS stub resolver: Denial of Service via long search domain in configuration |
| [CVE-2026-86805](https://avd.aquasec.com/nvd/cve-2026-86805) | Medium | glibc, glibc-common, glibc-minimal-langpack | 2.34-283.el9_8 | glibc: glibc: Privilege escalation and arbitrary code execution via TOCTOU race condition in dynamic loader |
| [CVE-2026-89092](https://avd.aquasec.com/nvd/cve-2026-89092) | Medium | glibc, glibc-common, glibc-minimal-langpack | 2.34-283.el9_8 | glibc: nscd stack overflow leads to degraded DNS resolution |
| [CVE-2026-95619](https://avd.aquasec.com/nvd/cve-2026-95619) | Medium | libgcc, libstdc++ | 11.5.0-14.el9 | gcc: libstdc++ integer overflow in `new` operator |
| [CVE-2026-95818](https://avd.aquasec.com/nvd/cve-2026-95818) | Medium | glibc, glibc-common, glibc-minimal-langpack | 2.34-283.el9_8 | glibc: glibc: Local attacker can cause denial of service and information disclosure via stack-based buffer overflow. |
| [CVE-2021-46195](https://avd.aquasec.com/nvd/cve-2021-46195) | Low | libgcc, libstdc++ | 11.5.0-14.el9 | gcc: uncontrolled recursion in libiberty/rust-demangle.c |
| [CVE-2022-27943](https://avd.aquasec.com/nvd/cve-2022-27943) | Low | libgcc, libstdc++ | 11.5.0-14.el9 | binutils: libiberty/rust-demangle.c in GNU GCC 11.2 allows stack exhaustion in demangle_const |
| [CVE-2022-41409](https://avd.aquasec.com/nvd/cve-2022-41409) | Low | pcre2 | 10.40-7.el9_8 | pcre2: negative repeat value in a pcre2test subject line leads to inifinite loop |
| [CVE-2023-50495](https://avd.aquasec.com/nvd/cve-2023-50495) | Low | ncurses-libs | 6.2-12.20210508.el9 | ncurses: segmentation fault via _nc_wrap_entry() |
| [CVE-2024-13176](https://avd.aquasec.com/nvd/cve-2024-13176) | Low | openssl-libs | 1:3.5.8-2.el9_8 | openssl: Timing side-channel in ECDSA signature computation |
| [CVE-2024-41996](https://avd.aquasec.com/nvd/cve-2024-41996) | Low | openssl-libs | 1:3.5.8-2.el9_8 | openssl: remote attackers (from the client side) to trigger unnecessarily expensive server-side DHE modular-exponentiation calculations |
| [CVE-2025-9232](https://avd.aquasec.com/nvd/cve-2025-9232) | Low | openssl-libs | 1:3.5.8-2.el9_8 | openssl: Out-of-bounds read in HTTP client no_proxy handling |
| [CVE-2026-27171](https://avd.aquasec.com/nvd/cve-2026-27171) | Low | zlib | 1.2.11-40.el9 | zlib: zlib: Denial of Service via infinite loop in CRC32 combine functions |
| [CVE-2026-28387](https://avd.aquasec.com/nvd/cve-2026-28387) | Low | openssl-libs | 1:3.5.8-2.el9_8 | openssl: OpenSSL: Arbitrary code execution due to use-after-free in DANE TLSA authentication |
| [CVE-2026-28388](https://avd.aquasec.com/nvd/cve-2026-28388) | Low | openssl-libs | 1:3.5.8-2.el9_8 | openssl: OpenSSL: Denial of Service due to NULL pointer dereference in delta CRL processing |
| [CVE-2026-28389](https://avd.aquasec.com/nvd/cve-2026-28389) | Low | openssl-libs | 1:3.5.8-2.el9_8 | openssl: OpenSSL: Denial of Service vulnerability in CMS processing |
| [CVE-2026-28753](https://avd.aquasec.com/nvd/cve-2026-28753) | Low | nginx, nginx-core, nginx-filesystem | 2:1.26.3-9.module+el9.8.0+24845+a897661e.4 | NGINX: NGINX Plus: NGINX Open Source: NGINX Plus and NGINX Open Source: Request manipulation via header injection in SMTP upstream requests |
| [CVE-2026-31789](https://avd.aquasec.com/nvd/cve-2026-31789) | Low | openssl-libs | 1:3.5.8-2.el9_8 | openssl: OpenSSL: Heap buffer overflow on 32-bit systems from large X.509 certificate processing |
| [CVE-2026-35189](https://avd.aquasec.com/nvd/cve-2026-35189) | Low | openssl-libs | 1:3.5.8-2.el9_8 | openssl: openssl: Denial of Service via excessive memory allocation in CRL distribution point processing |
| [CVE-2026-35191](https://avd.aquasec.com/nvd/cve-2026-35191) | Low | openssl-libs | 1:3.5.8-2.el9_8 | openssl: openssl: Traffic amplification Denial of Service via QUIC packet over-accounting |
| [CVE-2026-42765](https://avd.aquasec.com/nvd/cve-2026-42765) | Low | openssl-libs | 1:3.5.8-2.el9_8 | openssl: NULL Dereference in Certificate Verification with OCSP Checking |
| [CVE-2026-97399](https://avd.aquasec.com/nvd/cve-2026-97399) | Low | glibc, glibc-common, glibc-minimal-langpack | 2.34-283.el9_8 | glibc: glibc: Denial of Service via out-of-bounds read in strncasecmp |

## What was removed from the image

| Change | Effect |
|---|---|
| Python from upstream (python-build-standalone via uv), not the Red Hat RPM | No Python RPM findings (python3.12 had 17 CVEs, twice) |
| PostgreSQL 17.11 built from a checksum-pinned source tarball without libxml, gssapi, ldap, pam, icu, readline | Removed libxml2, krb5-libs, openldap, cyrus-sasl-lib, libtirpc, sqlite-libs findings |
| nginx 1.26 module stream (was 1.20.1 by mistake) | Cleared nginx 1.20 findings |
| Unused packages erased and their files deleted: systemd, dbus, PAM, shadow-utils, util-linux (incl. libmount, libblkid, libuuid), sed, expat, p11-kit, libffi, libtasn1, gzip, acl, bzip2-libs, openssl CLI, OpenSSL FIPS provider, ncurses-base, pcre2-syntax | Removed those packages' findings |
| coreutils swapped for coreutils-single | Dropped the 10 MB coreutils-common package |
| glibc-langpack-en no longer installed | Database uses the C.UTF-8 locale |

Files of erased packages that the ubi-micro base layer also ships are deleted explicitly in the final stage, so no unowned files remain that a scanner cannot see.

## Caveats

- **Unscanned bundled code.** Trivy does not look inside the upstream Python build (it bundles OpenSSL 3.5.7, SQLite 3.53.1, expat 2.8.1, zlib 1.3.2) or the psycopg2-binary wheel (it bundles its own libssl, Kerberos, LDAP and SASL libraries). They are not reported here and are not proven clean.
- **Pinned by hand.** PostgreSQL (PG_VERSION, PG_SHA256) and the Python version are pinned in the Dockerfile and need manual bumps for security releases. Nothing patches them automatically.
- **Config-dependent rulings.** The 'not exploitable as configured' rows stop applying if TLS, HTTP/2, HTTP/3, mail, ssi, aio or similar nginx features, or PostgreSQL ssl, are enabled.
- **Not covered.** Only the DRY_RUN_ONLY variant on ARM was built and scanned. The full image, x86, the compose files and old data volumes were not tested.
- **Locale.** glibc-common and glibc-minimal-langpack stay because they provide the C.utf8 locale; without it PostgreSQL creates a SQL_ASCII database and non-ASCII JIL content fails to import.
- **Verification run.** Host test suite: 1764 passed, 6 skipped. Container: JIL import, a dry-run job (status success) and a restart with data intact.
