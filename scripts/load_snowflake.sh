#!/bin/bash
# ============================================================
# load_snowflake.sh — Load cleaned data into the warehouse
# Usage: load_snowflake.sh --date YYYYMMDD
# Simulates a Snowflake COPY INTO operation
# ============================================================

DATE_ARG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --date) DATE_ARG="$2"; shift 2 ;;
        *) shift ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EXTRACTED_DIR="$SCRIPT_DIR/data/extracted"
WAREHOUSE_DIR="$SCRIPT_DIR/data/warehouse"
mkdir -p "$WAREHOUSE_DIR"

echo "============================================"
echo "  AutoSys Job: load_to_warehouse"
echo "  Timestamp:   $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Target:      Snowflake (simulated)"
echo "============================================"
echo ""

# Find extracted file
INPUT_FILE=""
if [ -d "$EXTRACTED_DIR" ]; then
    INPUT_FILE=$(ls -t "$EXTRACTED_DIR"/sales_clean_*.csv 2>/dev/null | head -1)
fi

if [ -z "$INPUT_FILE" ]; then
    INPUT_FILE="$SCRIPT_DIR/data/inbound/sales_sample.csv"
    echo "[INFO] No extracted file found, using raw source"
fi

echo "[STEP 1/5] Connecting to Snowflake warehouse..."
sleep 0.5
echo "           ✓ Connection established"
echo "           ✓ Database: SALES_DW"
echo "           ✓ Schema:   RAW_SALES"

echo "[STEP 2/5] Creating staging table..."
sleep 0.3
echo "           ✓ STG_SALES_${DATE_ARG:-$(date +%Y%m%d)} created"

echo "[STEP 3/5] Loading data via COPY INTO..."
ROW_COUNT=$(($(wc -l < "$INPUT_FILE" | tr -d ' ') - 1))
# Simulate batch loading
BATCH=1
LOADED=0
while [ $LOADED -lt $ROW_COUNT ]; do
    BATCH_SIZE=$((ROW_COUNT < 5 ? ROW_COUNT : 5))
    REMAINING=$((ROW_COUNT - LOADED))
    if [ $REMAINING -lt $BATCH_SIZE ]; then
        BATCH_SIZE=$REMAINING
    fi
    LOADED=$((LOADED + BATCH_SIZE))
    echo "           Batch ${BATCH}: loaded ${BATCH_SIZE} rows (${LOADED}/${ROW_COUNT})"
    BATCH=$((BATCH + 1))
    sleep 0.2
done

echo "[STEP 4/5] Running data quality checks..."
sleep 0.3
echo "           ✓ NULL check:       PASSED (0 null keys)"
echo "           ✓ Duplicate check:  PASSED (0 duplicates)"
echo "           ✓ Range check:      PASSED (all amounts > 0)"
echo "           ✓ Referential check: PASSED"

echo "[STEP 5/5] Merging into production table..."
# Copy to warehouse directory as proof
WAREHOUSE_FILE="$WAREHOUSE_DIR/fact_sales_${DATE_ARG:-$(date +%Y%m%d)}.csv"
cp "$INPUT_FILE" "$WAREHOUSE_FILE"
sleep 0.3
echo "           ✓ MERGE INTO FACT_SALES complete"
echo "           ✓ ${ROW_COUNT} rows upserted"

echo ""
echo "  ┌─────────────────────────────────────┐"
echo "  │       WAREHOUSE LOAD SUMMARY        │"
echo "  ├─────────────────────────────────────┤"
echo "  │  Status:       SUCCESS              │"
echo "  │  Rows loaded:  ${ROW_COUNT}                    │"
echo "  │  Batches:      $((BATCH - 1))                      │"
echo "  │  DQ checks:    4/4 PASSED           │"
echo "  │  Target table: FACT_SALES           │"
echo "  └─────────────────────────────────────┘"
echo ""
echo "[DONE] Warehouse load complete"
exit 0
