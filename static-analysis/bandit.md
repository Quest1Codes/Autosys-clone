# Bandit findings

Bandit 1.9.4, `-r autosys` — raw: `raw/bandit.json`.

**0 findings.** First scan of this session: 35 (3 high, 9 medium, 23 low).

Code was changed for the medium findings (http(s)-only `urllib`, loopback bind defaults, SQLAlchemy `update` instead of an f-string) and for the asserts and `try/except: pass` cases. The remaining ones are kept on purpose and marked with a `# nosec` comment and the reason next to it (13 `nosec` comments under `autosys/`): `shell=True` for job commands, plain FTP for FTP jobs, fixed-argument `subprocess` calls, a sentinel secret the server refuses to run with, the `"bearer"` token type name, and seeded simulation randomness. A suppressed finding is still worth a human look.
