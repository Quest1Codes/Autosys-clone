# Trivy image scans

Trivy 0.74.0, `image --scanners vuln,secret`. Raw: `raw/trivy-image-*.json`. UBI 10 (10.2) base on all four; none uses Alpine.

| Image | OS | Critical | High | Medium | Low | Secrets |
|---|---|---|---|---|---|---|
| autosys-clone:latest | redhat 10.2 | 0 | 0 | 0 | 0 | 0 |
| autosys-clone:dryrun-ubi10 | redhat 10.2 | 0 | 0 | 0 | 0 | 0 |
| autosys-clone-web:latest | redhat 10.2 | 0 | 0 | 0 | 0 | 0 |
| autosys-postgres:16 | redhat 10.2 | 0 | 0 | 0 | 0 | 0 |

The images were scanned and, for `autosys-postgres:16`, booted. The web image and the full `autosys-clone:latest` were not booted for this report.
