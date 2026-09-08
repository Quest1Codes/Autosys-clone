#!/usr/bin/env bash
# start_and_dry_run.sh — start the simulator, import all JIL files, then
# dry-run every top-level BOX job (non-mutating FSM trace via box_trace.py).
#
# Unlike scripts/load_and_run.sh (which fires real FORCE_STARTJOB events and
# needs a live Event Processor to consume them, mutating job state), this
# script uses autosys.analysis.box_trace.run_box_trace() for every box —
# each trace resets its own subtree to INACTIVE, runs it through the real
# scheduler FSM, and rolls back. Nothing is persisted. Safe to re-run anytime.
#
# Usage (run from the repo root or anywhere — path-independent):
#   bash scripts/start_and_dry_run.sh                 # start servers + import + dry run
#   bash scripts/start_and_dry_run.sh --start-frontend # also start the Vite dev server
#   bash scripts/start_and_dry_run.sh --skip-servers   # assume servers already running
#   bash scripts/start_and_dry_run.sh --skip-import    # assume JIL already loaded
#   bash scripts/start_and_dry_run.sh --stop           # stop everything this script started
#
# Ports (override via env): API_PORT=9000  WCC_PORT=8080  FRONTEND_PORT=5173

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JIL_DIR="$REPO_ROOT/jil_files"
LOG_DIR="$REPO_ROOT/.sim_logs"
mkdir -p "$LOG_DIR"

API_PORT="${API_PORT:-9000}"
WCC_PORT="${WCC_PORT:-8080}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

DO_SERVERS=true
DO_IMPORT=true
DO_DRY_RUN=true
DO_FRONTEND=false
STOP_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --skip-servers)  DO_SERVERS=false ;;
    --skip-import)   DO_IMPORT=false ;;
    --skip-dry-run)  DO_DRY_RUN=false ;;
    --start-frontend) DO_FRONTEND=true ;;
    --stop)          STOP_ONLY=true ;;
  esac
done

cd "$REPO_ROOT"

run_cli() {
  python3 -c "from autosys.cli.main import autosys; import sys; autosys(sys.argv[1:])" "$@"
}

port_listening() {
  lsof -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

wait_for_port() {
  local port=$1 name=$2 timeout=${3:-20}
  for _ in $(seq 1 "$timeout"); do
    port_listening "$port" && return 0
    sleep 1
  done
  echo "  ✗ $name did not come up on port $port within ${timeout}s — check $LOG_DIR" >&2
  exit 1
}

# ── --stop: kill servers this script started, then exit ────────────────────
if $STOP_ONLY; then
  for f in "$LOG_DIR"/api.pid "$LOG_DIR"/wcc.pid "$LOG_DIR"/frontend.pid; do
    [ -f "$f" ] || continue
    pid="$(cat "$f")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "Stopped PID $pid ($f)"
    fi
    rm -f "$f"
  done
  exit 0
fi

# ── 0. START SIMULATOR SERVERS ──────────────────────────────────────────────
if $DO_SERVERS; then
  echo ""
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║  Step 0 — Start simulator servers                   ║"
  echo "╚══════════════════════════════════════════════════════╝"

  if port_listening "$API_PORT"; then
    echo "  · API server already listening on :$API_PORT — leaving it alone"
  else
    echo "  · starting API server on :$API_PORT  (log: $LOG_DIR/api.log)"
    nohup autosys scheduler serve --port "$API_PORT" --host 127.0.0.1 \
      > "$LOG_DIR/api.log" 2>&1 &
    echo $! > "$LOG_DIR/api.pid"
    wait_for_port "$API_PORT" "API server"
  fi

  if port_listening "$WCC_PORT"; then
    echo "  · WCC/SSE server already listening on :$WCC_PORT — leaving it alone"
  else
    echo "  · starting WCC/SSE server on :$WCC_PORT  (log: $LOG_DIR/wcc.log)"
    nohup autosys scheduler wcc --port "$WCC_PORT" --host 127.0.0.1 \
      > "$LOG_DIR/wcc.log" 2>&1 &
    echo $! > "$LOG_DIR/wcc.pid"
    wait_for_port "$WCC_PORT" "WCC server"
  fi

  echo "  ✓ servers up  (stop later with: bash scripts/start_and_dry_run.sh --stop)"
fi

# ── 0b. START FRONTEND (opt-in — --start-frontend) ──────────────────────────
if $DO_FRONTEND; then
  echo ""
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║  Step 0b — Start frontend (Vite dev server)         ║"
  echo "╚══════════════════════════════════════════════════════╝"

  if port_listening "$FRONTEND_PORT"; then
    echo "  · frontend already listening on :$FRONTEND_PORT — leaving it alone"
  else
    if [ ! -d "$REPO_ROOT/wcc-frontend/node_modules" ]; then
      echo "  · node_modules missing — running npm install (this may take a minute)"
      (cd "$REPO_ROOT/wcc-frontend" && npm install > "$LOG_DIR/frontend-install.log" 2>&1)
    fi
    echo "  · starting Vite dev server on :$FRONTEND_PORT  (log: $LOG_DIR/frontend.log)"
    (cd "$REPO_ROOT/wcc-frontend" && nohup npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
     echo $! > "$LOG_DIR/frontend.pid")
    wait_for_port "$FRONTEND_PORT" "Frontend dev server"
  fi

  echo "  ✓ frontend up  →  http://localhost:$FRONTEND_PORT  (login admin/admin)"
fi

# ── 1. IMPORT ALL JIL FILES ──────────────────────────────────────────────────
if $DO_IMPORT; then
  echo ""
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║  Step 1 — Import JIL files (numbered order)         ║"
  echo "╚══════════════════════════════════════════════════════╝"

  failed=0
  for f in "$JIL_DIR"/*.jil; do
    fname="$(basename "$f")"
    printf "  %-50s " "$fname"
    if run_cli jil import "$f" 2>&1 | tail -1 | grep -qiE "inserted|updated|OK|0 jobs"; then
      echo "✓"
    else
      run_cli jil import "$f" || true
      echo "✗ (see above)"
      (( failed++ )) || true
    fi
  done

  echo ""
  echo "  Imported $(ls "$JIL_DIR"/*.jil | wc -l | tr -d ' ') files  |  $failed failed"
fi

# ── 2. DRY-RUN EVERY TOP-LEVEL BOX (non-mutating FSM trace) ────────────────
if $DO_DRY_RUN; then
  echo ""
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║  Step 2 — Dry-run every top-level BOX               ║"
  echo "╚══════════════════════════════════════════════════════╝"
  echo ""

  python3 - <<'PYEOF'
from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo
from autosys.analysis.box_trace import run_box_trace, BoxNotFoundError, NotABoxError

with sync_session() as s:
    rows = job_repo.list_all(s)

boxes = sorted(
    r.job_name for r in rows
    if (r.job_type or "").upper() == "BOX" and not r.box_name
)

if not boxes:
    print("  No top-level BOX jobs found — did the import succeed?")
    raise SystemExit(1)

results = []
for box_name in boxes:
    with sync_session() as s:
        try:
            trace = run_box_trace(s, box_name)
            s.rollback()
            results.append((box_name, trace.outcome, trace.tick_count, trace.wave_count, len(trace.jobs)))
        except (BoxNotFoundError, NotABoxError) as exc:
            s.rollback()
            results.append((box_name, f"ERROR:{type(exc).__name__}", "-", "-", "-"))

print(f"  {'Box':38s} {'Outcome':12s} {'Ticks':>6s} {'Waves':>6s} {'Jobs':>5s}")
print("  " + "-" * 68)
for box_name, outcome, ticks, waves, njobs in results:
    marker = "✓" if outcome == "SUCCESS" else "✗"
    print(f"  {marker} {box_name:36s} {outcome:12s} {str(ticks):>6s} {str(waves):>6s} {str(njobs):>5s}")

ok  = sum(1 for r in results if r[1] == "SUCCESS")
bad = len(results) - ok
print("  " + "-" * 68)
print(f"  {len(results)} boxes traced — {ok} SUCCESS, {bad} not-clean (FAILURE / INCOMPLETE / ERROR)")
if bad:
    print(f"\n  Not-clean boxes (worth checking their conditions/box_success rules):")
    for box_name, outcome, *_ in results:
        if outcome != "SUCCESS":
            print(f"    - {box_name}: {outcome}")
PYEOF
fi

echo ""
echo "Done."
if $DO_SERVERS || $DO_FRONTEND; then
  if ! $DO_FRONTEND; then
    echo "Servers left running. Frontend not started — rerun with --start-frontend, or: cd wcc-frontend && npm run dev"
  else
    echo "Everything left running: API :$API_PORT  WCC/SSE :$WCC_PORT  Frontend http://localhost:$FRONTEND_PORT (login admin/admin)"
  fi
  echo "Stop everything this script started: bash scripts/start_and_dry_run.sh --stop"
fi
