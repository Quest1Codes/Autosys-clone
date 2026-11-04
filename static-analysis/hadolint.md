# hadolint findings

hadolint 2.15.1, `hadolint Dockerfile deploy/postgres/Dockerfile` (config: `.hadolint.yaml`) — raw: `raw/hadolint.json` (empty array, because nothing is reported now). The first-run output, taken from the committed Dockerfiles with no ignore config, is in `raw/hadolint-first-scan.json` and `raw/hadolint-first-scan-postgres.json`.

**0 findings.** First run: 17 (14 warnings, 3 info; no errors).

| Rule | Count | Resolution |
|---|---|---|
| DL4006 (no `pipefail`) | 6 | Fixed: `SHELL ["/bin/bash", "-o", "pipefail", "-c"]` in the build stages that use pipes (`pgbuild`, `rootfs`) of both Dockerfiles |
| DL3025 (`HEALTHCHECK CMD` not in JSON form) | 2 | Fixed: exec form for the web image (curl) and the Postgres image (`pg_isready` through `bash -c`) |
| DL3066 (`USER` not numeric) | 1 | Fixed: `USER 999:999` (the `nginx` user's uid/gid in the UBI 10 image) |
| SC2016 (single quotes) | 2 | Suppressed in place with a reason: the script is meant to expand inside the chroot, and nginx's `$variables` must reach `nginx.conf` unexpanded |
| DL3041 (unpinned `dnf install`) | 7 | Ignored in `.hadolint.yaml` with a reason: base images are pinned to `10.2`, and exact RPM pins would block security fixes until bumped by hand |

After the changes the web and Postgres images were rebuilt and booted: both report `healthy`, the web container runs as `999:999`, and Trivy shows 0 findings on all four images.
