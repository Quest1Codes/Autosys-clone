#!/bin/bash
# ============================================================
# send_email.sh — Send completion notification
# Usage: send_email.sh "message body"
# Simulates sending an email notification
# ============================================================

MESSAGE="$1"

echo "============================================"
echo "  AutoSys Job: send_success_email"
echo "  Timestamp:   $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================"
echo ""
echo "[STEP 1/3] Preparing email notification..."
echo "           To:      data-ops@quest1works.com"
echo "           CC:      etl-alerts@quest1works.com"
echo "           Subject: [AutoSys] ETL Pipeline Complete"
echo ""
echo "[STEP 2/3] Composing message body..."
echo "           ┌──────────────────────────────────────┐"
echo "           │  $MESSAGE"
echo "           │                                      │"
echo "           │  Pipeline:  demo_etl_box             │"
echo "           │  Status:    ALL JOBS SUCCEEDED        │"
echo "           │  Time:      $(date '+%Y-%m-%d %H:%M:%S')     │"
echo "           │  Server:    $(hostname)               │"
echo "           └──────────────────────────────────────┘"
echo ""
echo "[STEP 3/3] Sending via SMTP..."
sleep 0.3
echo "           ✓ Email sent successfully"
echo ""
echo "[DONE] Notification delivered"
exit 0
