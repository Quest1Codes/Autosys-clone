# Semgrep findings

Semgrep 1.179.0, `--config p/default` (skipped `node_modules`, `dist`, `reports`, `.venv`, `static-analysis`) — raw: `raw/semgrep.json`. 7 parse errors mean some files were scanned only partly.

**1 finding:** INFO 1

The first `p/default` run found 13. Everything except the item below was fixed or, where the code is already safe, marked `nosemgrep` with the reason next to it.

| Sev | Rule | File:line | Message |
|---|---|---|---|
| INFO | use-ftp-tls | autosys/agent/runners.py:154 | The 'FTP' class sends information unencrypted. Consider using the 'FTP_TLS' class instead. |

Not fixed on purpose: `use-ftp-tls` says to use `ftplib.FTP_TLS`. An FTP job talks to whatever server the job names, and many are plain FTP; switching would break them. Making TLS an opt-in job attribute would be a feature change.
