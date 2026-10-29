# Synthetic run-history test files

**Test fixtures for exercising `autosys history import` / WCC's "Import Run
History" upload. Not client deliverables, not evidence about any real
estate.** Generated from this repo's own `jil_files/` demo estate, not from
a real client export — see `dev/task9-cost-calculator/05-cost-estimation-questions.md`
S21 for what a real export's provenance and format actually look like.

| File | What |
|---|---|
| `generate_synthetic_history.py` | The generator. Reads the real job list from `AUTOSYS_DB_URL`, writes a CSV in the run-history import contract (`autosys/parser/run_history_ingest.py`'s module docstring). |
| `synthetic_run_history_247jobs.csv` | Output for the 247 non-BOX jobs in this repo's local demo estate (`jil_files/`). 60-day window, failure rates loosely keyed off each job's real `n_retrys`, with two jobs forced into clearly-HIGH / unanimous-failure outcomes so the full risk range (HIGH/MEDIUM/LOW/NONE) is demonstrated rather than left to chance at this estate's small `n_retrys>=3` sample size (2 jobs). |

## Regenerating

```bash
# Against the local dev stack's Postgres (from the repo root, with the
# simulator containers up):
docker run --rm --network autosys-clone_default \
  -e AUTOSYS_DB_URL='postgresql://autosys:autosys@postgres:5432/autosys' \
  -v "$PWD/autosys:/app/autosys:ro" \
  -v "$PWD/run_history_files:/work" -w /work \
  autosys-clone:latest python3 generate_synthetic_history.py

# Against a local SQLite DB:
AUTOSYS_DB_URL=sqlite:///path/to.db python3 generate_synthetic_history.py
```

Same seed (`--seed`, default fixed) plus the same job list reproduces the
same profile mix every time; exact timestamps shift slightly between runs
since the window is computed from the current time, not a fixed date.

## Using it

Drag `synthetic_run_history_247jobs.csv` onto WCC's "Import Run History"
modal, or:

```bash
autosys history import run_history_files/synthetic_run_history_247jobs.csv
```

No `--timezone` needed (timestamps carry an explicit UTC offset). Re-running
the same import is a no-op (idempotent re-import, see the importer's
`run_id` hashing) — safe to try more than once.
