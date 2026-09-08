#!/usr/bin/env bash
# load_and_run.sh — import all JIL files in order then start every top-level BOX
#
# Usage:
#   ./scripts/load_and_run.sh                   # import + run
#   ./scripts/load_and_run.sh --import-only     # import only, don't fire events
#   ./scripts/load_and_run.sh --run-only        # skip import, just start boxes
#
# Run from the repo root:
#   cd /Users/Raghav/Quest1/autosys-astronomer/Autosys-2.0/Autosys-clone
#   bash scripts/load_and_run.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JIL_DIR="$REPO_ROOT/jil_files"
CLI="python3 -c 'from autosys.cli.main import autosys; import sys; autosys(sys.argv[1:])'"

# ── flags ──────────────────────────────────────────────────────────────────
DO_IMPORT=true
DO_RUN=true
for arg in "$@"; do
  case "$arg" in
    --import-only) DO_RUN=false ;;
    --run-only)    DO_IMPORT=false ;;
  esac
done

cd "$REPO_ROOT"

# ── helper ─────────────────────────────────────────────────────────────────
run_cli() {
  python3 -c "from autosys.cli.main import autosys; import sys; autosys(sys.argv[1:])" "$@"
}

# ── 1. IMPORT ───────────────────────────────────────────────────────────────
if $DO_IMPORT; then
  echo ""
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║  Step 1 — Import JIL files (numbered order)         ║"
  echo "╚══════════════════════════════════════════════════════╝"

  total=0
  failed=0
  for f in "$JIL_DIR"/*.jil; do
    fname="$(basename "$f")"
    printf "  %-50s " "$fname"
    if run_cli jil import "$f" 2>&1 | tail -1 | grep -qiE "inserted|updated|OK|0 jobs"; then
      echo "✓"
      (( total++ )) || true
    else
      # re-run to show actual output on failure
      run_cli jil import "$f" || true
      echo "✗ (see above)"
      (( failed++ )) || true
    fi
  done

  echo ""
  echo "  Imported $(ls "$JIL_DIR"/*.jil | wc -l | tr -d ' ') files  |  $failed failed"
fi

# ── 2. CONFIRM JOB COUNT ────────────────────────────────────────────────────
if $DO_IMPORT; then
  echo ""
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║  Step 2 — Job inventory                             ║"
  echo "╚══════════════════════════════════════════════════════╝"
  run_cli autorep -J '%' 2>&1 | tail -5
fi

# ── 3. FIRE FORCE_STARTJOB ON ALL TOP-LEVEL BOXES ──────────────────────────
if $DO_RUN; then
  echo ""
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║  Step 3 — Start all top-level BOX jobs              ║"
  echo "╚══════════════════════════════════════════════════════╝"

  # Collect top-level BOX names from the DB
  BOXES=$(python3 - <<'PYEOF'
from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo
with sync_session() as s:
    rows = job_repo.list_all(s)
boxes = sorted(
    r.job_name for r in rows
    if (r.job_type or '').upper() == 'BOX' and not r.box_name
)
print('\n'.join(boxes))
PYEOF
)

  if [ -z "$BOXES" ]; then
    echo "  No top-level BOX jobs found — did the import succeed?"
    exit 1
  fi

  count=0
  while IFS= read -r box; do
    printf "  FORCE_STARTJOB → %-45s " "$box"
    run_cli sendevent -E force_startjob -J "$box" 2>&1 | grep -oE "queued|ERROR|error" | head -1 || echo ""
    echo "✓"
    (( count++ )) || true
  done <<< "$BOXES"

  echo ""
  echo "  Sent FORCE_STARTJOB to $count BOX jobs."
  echo "  The Event Processor will pick them up on its next poll tick."
  echo "  Open http://localhost:5173 to watch execution."
fi

echo ""
echo "Done."
