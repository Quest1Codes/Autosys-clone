#!/usr/bin/env python3
"""
gen_report.py — Generate a sales report from extracted data.
Usage: gen_report.py --type sales --date YYYYMMDD
"""

import argparse
import csv
import os
import sys
from datetime import datetime
from collections import defaultdict

def main():
    parser = argparse.ArgumentParser(description="Generate sales report")
    parser.add_argument("--type", default="sales", help="Report type")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="Report date")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Find the extracted file
    extracted_dir = os.path.join(script_dir, "data", "extracted")
    report_dir = os.path.join(script_dir, "data", "reports")
    os.makedirs(report_dir, exist_ok=True)

    # Find most recent extracted file
    input_file = None
    if os.path.isdir(extracted_dir):
        files = sorted([f for f in os.listdir(extracted_dir) if f.startswith("sales_clean")])
        if files:
            input_file = os.path.join(extracted_dir, files[-1])
    
    if not input_file or not os.path.exists(input_file):
        # Fall back to raw sample
        input_file = os.path.join(script_dir, "data", "inbound", "sales_sample.csv")

    print("=" * 50)
    print(f"  AutoSys Job: generate_report")
    print(f"  Timestamp:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Report Type: {args.type}")
    print(f"  Report Date: {args.date}")
    print("=" * 50)
    print()

    if not os.path.exists(input_file):
        print(f"[ERROR] No data file found at {input_file}")
        sys.exit(1)

    print(f"[STEP 1/3] Reading data from {os.path.basename(input_file)}...")

    # Parse CSV
    rows = []
    with open(input_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    
    print(f"           Loaded {len(rows)} records")

    print("[STEP 2/3] Analyzing data...")

    # Revenue by region
    region_revenue = defaultdict(float)
    category_revenue = defaultdict(float)
    customer_spend = defaultdict(float)
    
    total_revenue = 0.0
    for row in rows:
        amount = float(row.get("total", 0))
        total_revenue += amount
        region_revenue[row.get("region", "Unknown")] += amount
        category_revenue[row.get("category", "Unknown")] += amount
        customer_spend[row.get("customer_name", "Unknown")] += amount

    print("[STEP 3/3] Generating report...")

    # Build report
    report_lines = []
    report_lines.append(f"{'=' * 55}")
    report_lines.append(f"  DAILY SALES REPORT — {args.date}")
    report_lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append(f"{'=' * 55}")
    report_lines.append("")
    report_lines.append(f"  OVERVIEW")
    report_lines.append(f"  ─────────────────────────────────")
    report_lines.append(f"  Total Transactions:  {len(rows)}")
    report_lines.append(f"  Total Revenue:       ${total_revenue:,.2f}")
    report_lines.append(f"  Average Deal Size:   ${total_revenue/len(rows):,.2f}")
    report_lines.append(f"  Unique Customers:    {len(customer_spend)}")
    report_lines.append("")
    report_lines.append(f"  REVENUE BY REGION")
    report_lines.append(f"  ─────────────────────────────────")
    for region, rev in sorted(region_revenue.items(), key=lambda x: -x[1]):
        pct = (rev / total_revenue) * 100
        bar = "█" * int(pct / 5)
        report_lines.append(f"  {region:<18} ${rev:>12,.2f}  ({pct:5.1f}%) {bar}")
    report_lines.append("")
    report_lines.append(f"  REVENUE BY CATEGORY")
    report_lines.append(f"  ─────────────────────────────────")
    for cat, rev in sorted(category_revenue.items(), key=lambda x: -x[1]):
        pct = (rev / total_revenue) * 100
        report_lines.append(f"  {cat:<18} ${rev:>12,.2f}  ({pct:5.1f}%)")
    report_lines.append("")
    report_lines.append(f"  TOP CUSTOMERS")
    report_lines.append(f"  ─────────────────────────────────")
    for name, spend in sorted(customer_spend.items(), key=lambda x: -x[1])[:5]:
        report_lines.append(f"  {name:<20} ${spend:>12,.2f}")
    report_lines.append("")
    report_lines.append(f"{'=' * 55}")

    report_text = "\n".join(report_lines)

    # Save report to file
    report_file = os.path.join(report_dir, f"sales_report_{args.date}.txt")
    with open(report_file, "w") as f:
        f.write(report_text)

    # Print report to stdout (captured by AutoSys agent)
    print()
    print(report_text)
    print()
    print(f"[DONE] Report saved to {os.path.basename(report_file)}")

if __name__ == "__main__":
    main()
