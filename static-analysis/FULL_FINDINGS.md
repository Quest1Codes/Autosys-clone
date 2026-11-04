# Static-analysis findings and fixes

Scan date 2026-11-03. Tools: osv-scanner 2.6.0, Trivy 0.74.0, Bandit 1.9.4, Semgrep 1.179.0 (`p/default`), hadolint 2.15.1.
Per-tool reports: `osv-scanner.md`, `trivy.md`, `trivy-images.md`, `bandit.md`, `semgrep.md`, `hadolint.md`, `gitleaks.md`. Raw output: `raw/`.
The per-tool reports are from the latest re-scan, after the fixes below. ESLint is not set up in this repo and was not run.

## Scan results: first scan -> latest
| Tool | First scan | Latest |
|---|---|---|
| osv-scanner | 13 open (2 critical, 5 high, 5 medium, 1 low, incl. Dockerfile checks) | 0 |
| Trivy fs (vulnerabilities / misconfigurations / secrets) | the same 13 overlap (the Dockerfile checks are Trivy findings) | 0 / 0 / 0 |
| Trivy images (4, rebuilt) | clean on UBI 10 | clean (see `trivy-images.md`) |
| Bandit | 35 (3 high, 9 medium, 23 low) | 0 (13 suppressed with a reason) |
| Semgrep, packs python/security-audit/dockerfile/javascript/secrets | 6 | 0 |
| Semgrep `p/default` | 13 | 1 (INFO `use-ftp-tls`, left on purpose) |
| hadolint | 17 (14 warnings, 3 info) | 0 (DL3041 ignored in `.hadolint.yaml`, 2 SC2016 suppressed, rest fixed) |
| Gitleaks (history, 87 commits) | 2 (reviewed false positives) | 0 (`.gitleaksignore`; the docs example was also reworded) |
| Gitleaks (working tree) | | 1: the local, gitignored `.env`, never committed |
| Tests | | 1833 passed, 6 skipped |

## Fixed
| Finding | Fix |
|---|---|
| certifi 2026.7.22 (critical, 2 advisories) | certifi 2026.10.23 in `uv.lock` |
| follow-redirects 1.16.0 (3 high) | 1.16.2 in `wcc-frontend/package-lock.json` |
| vite 6.4.3 (1 high, 3 medium, 1 low) | vite 6.4.5 |
| Dockerfile DS-0001, DS-0013, DS-0019 | pinned `:10.2` base tags, `WORKDIR` instead of `RUN cd`, `dnf clean all` |
| Alpine images | all runtime images moved to UBI 10 (app, web, Postgres built from source) |
| Bandit B310 / Semgrep `dynamic-urllib-use-detected` (5 places) | new `autosys/netutil.py` allows only http(s); all call sites use it |
| Bandit B104 (4) | bind defaults `127.0.0.1`; compose files and the entrypoint pass `--host 0.0.0.0` explicitly |
| Bandit B608 `db/migrations.py` | backfill uses a SQLAlchemy `update` (lightweight table/column constructs, no ORM defaults) |
| Bandit B101 (3), B110 (8) | explicit errors instead of `assert`; swallowed exceptions logged at debug level |
| Semgrep `sqlalchemy-execute-raw-query` (2, `scripts/jil_stress/verify.py`) | Table/column names from the corpus manifest are validated (`[A-Za-z_][A-Za-z0-9_]*`) and quoted by a new `_ident` helper; the IN-list comes from `_in_clause` (only `?` placeholders); values stay bound parameters. Two `nosemgrep` comments remain on the `execute` calls with the reason. Helpers checked on SQLite; the full stress run was not executed |
| Semgrep `dependabot-missing-cooldown` (5) | `cooldown: default-days: 7` on every ecosystem in `.github/dependabot.yml` (security updates skip it) |
| Semgrep `missing-integrity` (2, `explain/index.html`) | Pinned the CDN scripts (`mermaid@12.2.0`, `@phosphor-icons/web@2.1.2`) and added SRI `sha384` hashes. The page was not opened in a browser. The icon loader script adds six stylesheets from jsDelivr, which carry no SRI |
| Semgrep `possible-nginx-h2c-smuggling` (2) | Already mitigated by the `map` that forwards `Upgrade` only when it is exactly `websocket`; marked `nosemgrep` with that reason. Both configs pass `nginx -t` |
| Semgrep `non-literal-import` (`autosys/agent/__init__.py`) | Module path comes from the fixed `_LAZY` table, not the caller; marked `nosemgrep` with that reason |
| hadolint DL4006 (6), DL3025 (2), DL3066 (1) | `SHELL ["/bin/bash","-o","pipefail","-c"]` in the pipe-using build stages, exec-form `HEALTHCHECK`, `USER 999:999`. Web and Postgres images rebuilt and booted: both healthy |

| Gitleaks `curl-auth-user` (`docs/02-astronomer-guide.md`) | Example now uses `"$AIRFLOW_USER:$AIRFLOW_PASSWORD"`; old line is in history, so its fingerprint is in `.gitleaksignore` |
| Gitleaks `generic-api-key` (`scripts/jil_stress/pdf_examples.json`) | Reviewed: a sample key from vendor documentation. In `.gitleaksignore` with the reason |

## Suppressed with a reason, not changed
`shell=True` in `agent/runner.py` (a job command is a shell line by design), `ftplib` in `agent/runners.py` (plain FTP is what an FTP job asks for), fixed-argument `subprocess` calls, the sentinel secret in `auth.py`, the `"bearer"` token type, seeded random in `failure_injector.py`. hadolint DL3041 (unpinned `dnf install`) is ignored in `.hadolint.yaml`: the bases are pinned to `10.2` and exact RPM pins would block security fixes. A suppressed finding is still worth a human look.

## Not fixed, and why
- **Semgrep `p/default`, 1 info finding:** `use-ftp-tls` in `autosys/agent/runners.py`. Plain FTP is what an FTP job asks for; FTP over TLS would break jobs against plain-FTP servers.
- **Ruff:** 907 style findings, not addressed.
- **Gitleaks, working tree:** the local `.env` (gitignored, never committed, holds a JWT secret and users). Not a repo leak; keep it out of images and shares. **ESLint:** not configured in `wcc-frontend`.
- **Not booted:** the full `autosys-clone:latest`; the JIL import was not re-run after the last Dockerfile change.
