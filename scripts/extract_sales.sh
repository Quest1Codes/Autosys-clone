#!/bin/bash
# ============================================================
# extract_sales.sh — Extract and clean sales data from CSV
# Usage: extract_sales.sh --date YYYYMMDD
# Reads raw CSV, validates, deduplicates, writes clean output
# ============================================================

DATE_ARG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --date) DATE_ARG="$2"; shift 2 ;;
        *) shift ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INPUT_FILE="$SCRIPT_DIR/data/inbound/sales_sample.csv"
OUTPUT_DIR="$SCRIPT_DIR/data/extracted"
mkdir -p "$OUTPUT_DIR"
OUTPUT_FILE="$OUTPUT_DIR/sales_clean_${DATE_ARG:-$(date +%Y%m%d)}.csv"

echo "============================================"
echo "  AutoSys Job: extract_sales"
echo "  Timestamp:   $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Run Date:    ${DATE_ARG:-$(date +%Y%m%d)}"
echo "============================================"
echo ""

if [ ! -f "$INPUT_FILE" ]; then
    echo "[ERROR] Input file not found: $INPUT_FILE"
    exit 1
fi

echo "[STEP 1/4] Reading source file..."
TOTAL_LINES=$(($(wc -l < "$INPUT_FILE" | tr -d ' ') - 1))
echo "           Found ${TOTAL_LINES} raw records"

echo "[STEP 2/4] Validating data integrity..."
# Check for required columns
HEADER=$(head -1 "$INPUT_FILE")
REQUIRED_COLS=("transaction_id" "date" "customer_id" "total" "region")
for col in "${REQUIRED_COLS[@]}"; do
    if echo "$HEADER" | grep -q "$col"; then
        echo "           ✓ Column '$col' present"
    else
        echo "           ✗ Column '$col' MISSING"
        exit 1
    fi
done

echo "[STEP 3/4] Extracting and cleaning data..."
# Copy header + sort + deduplicate by transaction_id
head -1 "$INPUT_FILE" > "$OUTPUT_FILE"
tail -n +2 "$INPUT_FILE" | sort -t',' -k1,1 -u >> "$OUTPUT_FILE"
CLEAN_COUNT=$(($(wc -l < "$OUTPUT_FILE" | tr -d ' ') - 1))
echo "           Extracted ${CLEAN_COUNT} clean records"

echo "[STEP 4/4] Computing summary statistics..."
# Calculate total revenue using awk
TOTAL_REVENUE=$(tail -n +2 "$OUTPUT_FILE" | awk -F',' '{sum += $9} END {printf "%.2f", sum}')
UNIQUE_CUSTOMERS=$(tail -n +2 "$OUTPUT_FILE" | awk -F',' '{print $3}' | sort -u | wc -l | tr -d ' ')
REGIONS=$(tail -n +2 "$OUTPUT_FILE" | awk -F',' '{print $10}' | sort -u | tr '\n' ', ' | sed 's/,$//')

echo ""
echo "  ┌─────────────────────────────────────┐"
echo "  │       EXTRACTION SUMMARY            │"
echo "  ├─────────────────────────────────────┤"
echo "  │  Records extracted:  ${CLEAN_COUNT}               │"
echo "  │  Total revenue:      \$${TOTAL_REVENUE}     │"
echo "  │  Unique customers:   ${UNIQUE_CUSTOMERS}                │"
echo "  │  Regions:            ${REGIONS}"
echo "  │  Output file:        $(basename $OUTPUT_FILE)"
echo "  └─────────────────────────────────────┘"
echo ""
echo "[DONE] Extraction complete — ${CLEAN_COUNT} records written to $OUTPUT_FILE"
exit 0
