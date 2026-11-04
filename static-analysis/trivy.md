# Trivy filesystem scan findings

Trivy 0.74.0, `fs --scanners vuln,secret,misconfig` (skipped `node_modules`, `.venv`, `reports`, `wcc-frontend/dist`, `static-analysis`) — raw: `raw/trivy-fs.json`.

- Vulnerabilities: 0
- Misconfigurations: 0 (3 Dockerfile checks fixed: DS-0001 pinned base tags, DS-0013 `WORKDIR`, DS-0019 `dnf clean all`)
- Secrets: 0

No suppression file is used.
