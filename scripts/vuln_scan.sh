#!/usr/bin/env bash
# Vulnerability scan: codebase (osv-scanner + trivy fs) and Docker images (trivy image).
#
#   scripts/vuln_scan.sh              # scan everything, write reports/vuln/
#   scripts/vuln_scan.sh --build      # (re)build the images first
#   scripts/vuln_scan.sh --no-images  # codebase only
#
# Raw scanner output lands in reports/vuln/raw/. scripts/vuln_report.py merges it
# into reports/vuln/findings.json (+ a history snapshot so "fixed since last scan"
# works) which the HTML dashboard consumes.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/reports/vuln"
RAW="$OUT/raw"
BASELINE_REF="${BASELINE_REF:-}"   # optional: git ref whose lockfiles count as the "before" state
IMAGES=("autosys-clone:latest" "autosys-clone-web:latest" "autosys-postgres:16")
BUILD=0; SCAN_IMAGES=1
for a in "$@"; do
  case "$a" in
    --build) BUILD=1 ;;
    --no-images) SCAN_IMAGES=0 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

cd "$ROOT"
mkdir -p "$RAW"
rm -f "$RAW"/*.json

if [[ $BUILD -eq 1 && $SCAN_IMAGES -eq 1 ]]; then
  docker build --target runtime       -t autosys-clone:latest     . || exit 1
  docker build --target nginx-runtime -t autosys-clone-web:latest . || exit 1
fi

echo "==> osv-scanner (source)"
# exit code 1 just means "vulns found"
osv-scanner scan source --recursive --format json --output-file "$RAW/osv-source.json" \
  --allow-no-lockfiles . 2>"$RAW/osv-source.err" || true

if [[ -n "$BASELINE_REF" ]]; then
  echo "==> osv-scanner (baseline lockfiles @ $BASELINE_REF)"
  BASE_DIR="$(mktemp -d)"
  git ls-tree -r --name-only "$BASELINE_REF" | grep -E '(^|/)(package-lock\.json|requirements.*\.txt|poetry\.lock|uv\.lock)$' |
    while read -r f; do mkdir -p "$BASE_DIR/$(dirname "$f")"; git show "$BASELINE_REF:$f" > "$BASE_DIR/$f"; done
  osv-scanner scan source --recursive --format json --output-file "$RAW/osv-baseline.json" "$BASE_DIR" 2>/dev/null || true
  rm -rf "$BASE_DIR"
fi

echo "==> trivy fs (vuln + secret + misconfig)"
trivy fs --scanners vuln,secret,misconfig --skip-dirs node_modules,.venv,reports,wcc-frontend/dist \
  --format json --output "$RAW/trivy-fs.json" . || true

if [[ $SCAN_IMAGES -eq 1 ]]; then
  for img in "${IMAGES[@]}"; do
    echo "==> trivy image $img"
    name="trivy-image-$(echo "$img" | tr ':/' '__')"
    trivy image --scanners vuln,secret --format json --output "$RAW/$name.json" "$img" || true
  done
fi

echo "==> merging"
python3 "$ROOT/scripts/vuln_report.py" "$OUT"
echo "Done: $OUT/findings.json"
