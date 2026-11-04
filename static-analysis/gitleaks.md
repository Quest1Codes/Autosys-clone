# Gitleaks findings

Gitleaks 8.30.1, run twice with `--redact` (values are never printed): `gitleaks detect .` over the full git history (87 commits; raw: `raw/gitleaks.json`) and `gitleaks dir .` over the working tree (raw: `raw/gitleaks-worktree.json`). Allow-list: `.gitleaksignore`.

- **History: 0 findings** (2 on the first run, both reviewed false positives, now in `.gitleaksignore`).
- **Working tree: 1 finding**, the local `.env` (rule `generic-api-key`, line 1). The file is gitignored (`.gitignore:84`), has never been committed (`git log -- .env` is empty) and holds local settings such as `AUTOSYS_JWT_SECRET`. It is not a repository leak. Don't copy it into an image or share it.

## First run, history scan (2)

| Rule | File:line | Commit | Verdict |
|---|---|---|---|
| curl-auth-user | docs/02-astronomer-guide.md:693 | f08f879f | `-u admin:admin` in a curl example (Airflow's default login). **Fixed:** the doc now uses `"$AIRFLOW_USER:$AIRFLOW_PASSWORD"`. The old line stays in history, so its fingerprint is allow-listed |
| generic-api-key | scripts/jil_stress/pdf_examples.json:32 | eb131cdb | `xkey_to_manager` in a sample JIL stanza copied from the vendor docs; not a credential. Allow-listed |
