#!/bin/bash
# ============================================================
# check_file.sh — Verify source data file exists and is valid
# Usage: check_file.sh <filepath>
# Exit 0 if file exists and has data, Exit 1 if missing/empty
# ============================================================

FILE="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "============================================"
echo "  AutoSys Job: check_source_ready"
echo "  Timestamp:   $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================"
echo ""

# The %%DATE%% in the JIL gets expanded, but we also handle the sample file
# Try the exact file first, then fall back to sample
if [ -f "$FILE" ]; then
    ACTUAL_FILE="$FILE"
elif [ -f "$SCRIPT_DIR/data/inbound/sales_sample.csv" ]; then
    ACTUAL_FILE="$SCRIPT_DIR/data/inbound/sales_sample.csv"
    echo "[INFO] Using sample file: $ACTUAL_FILE"
else
    echo "[ERROR] Source file not found: $FILE"
    echo "[ERROR] Sample file also not found"
    echo "[FAIL] Pre-check FAILED"
    exit 1
fi

echo "[CHECK] Verifying file: $ACTUAL_FILE"

# Check file exists
if [ ! -f "$ACTUAL_FILE" ]; then
    echo "[ERROR] File does not exist!"
    exit 1
fi

# Check file is not empty
FILE_SIZE=$(wc -c < "$ACTUAL_FILE" | tr -d ' ')
if [ "$FILE_SIZE" -eq 0 ]; then
    echo "[ERROR] File is empty (0 bytes)!"
    exit 1
fi

# Count rows (excluding header)
ROW_COUNT=$(($(wc -l < "$ACTUAL_FILE" | tr -d ' ') - 1))

# Validate CSV header
HEADER=$(head -1 "$ACTUAL_FILE")
if echo "$HEADER" | grep -q "transaction_id"; then
    echo "[CHECK] CSV header validation: PASSED"
else
    echo "[ERROR] Invalid CSV header — expected 'transaction_id' column"
    exit 1
fi

echo "[CHECK] File size: ${FILE_SIZE} bytes"
echo "[CHECK] Data rows: ${ROW_COUNT}"
echo "[CHECK] Min rows required: 1"
echo ""
echo "[PASS] Source data is READY — ${ROW_COUNT} transactions found"
exit 0
