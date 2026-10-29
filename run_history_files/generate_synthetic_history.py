#!/usr/bin/env python3
"""
Generate a realistic synthetic run-history CSV for testing
`autosys history import` / WCC's "Import Run History" upload -- NOT a
client deliverable and NOT evidence about any real estate. It exists to
exercise the importer against a plausible-looking file before a real
client export is available.

Reads the REAL job list from AUTOSYS_DB_URL (job_name, job_type, machine,
n_retrys) and, for each non-BOX job, generates run history over a 60-day
window with a failure profile loosely keyed off the job's own n_retrys --
mirroring the same signal FailureInjector uses, but this is a *generated
test fixture*, not a claim about what the real jobs actually did.

Usage
-----
    AUTOSYS_DB_URL=postgresql://... python3 generate_synthetic_history.py
    AUTOSYS_DB_URL=sqlite:///path/to.db python3 generate_synthetic_history.py \\
        --output my_history.csv --window-days 90 --seed 7

Columns: job_name, status, start_time, end_time, exit_code, machine,
attempt. run_id is deliberately omitted so an import of this file exercises
the start_time-based idempotency key -- the more realistic case for a DBA
export that has no identifier of its own.

Intraday-named jobs (job_name contains "intraday") get 4 runs/day instead
of 1, matching what such a job would realistically run in production.
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

FALLBACK_MACHINES = ["app-01", "app-02", "batch-01", "batch-02"]


def profile_for(n_retrys: int, forced_profiles: list) -> dict:
    """
    A failure-rate profile, loosely keyed off n_retrys -- real sites
    configure more retries on jobs they already know are flaky, so this
    mirrors that correlation without claiming to measure it.

    *forced_profiles* lets the caller guarantee at least one job lands in
    each demonstrative tier (see main()) rather than leaving it to a coin
    flip -- at low n_retrys>=3 sample sizes, random chance can easily
    produce zero HIGH-risk examples, which would leave that part of any UI
    built on this data untested.
    """
    if n_retrys >= 3:
        if forced_profiles:
            return forced_profiles.pop(0)
        return {"fail_rate": random.uniform(0.25, 0.40), "name": "flaky"}
    if n_retrys >= 1:
        return {"fail_rate": random.uniform(0.05, 0.15), "name": "occasional"}
    return {"fail_rate": random.uniform(0.0, 0.03), "name": "healthy"}


def runs_per_day(job_name: str) -> int:
    return 4 if "intraday" in job_name.lower() else 1


def fetch_jobs() -> list[dict]:
    """Real job list from AUTOSYS_DB_URL -- job_name, job_type, machine,
    n_retrys. Reuses the project's own DB layer rather than a separate
    export step, so this stays correct as the schema evolves."""
    from autosys.db.connection import sync_session
    from autosys.db.repository import jobs as job_repo
    with sync_session() as session:
        rows = job_repo.list_all(session)
        return [{"job_name": r.job_name, "job_type": (r.job_type or "CMD").upper(),
                 "machine": r.machine or "", "n_retrys": r.n_retrys or 0}
                for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", default=None,
                    help="Output CSV path (default: synthetic_run_history_<N>jobs.csv "
                         "next to this script)")
    ap.add_argument("--window-days", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20261029,
                    help="Reproducible by default -- same seed, same file.")
    args = ap.parse_args()

    random.seed(args.seed)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=args.window_days)

    jobs = fetch_jobs()
    non_box = [j for j in jobs if j["job_type"] != "BOX"]
    out_path = Path(args.output) if args.output else (
        Path(__file__).parent / f"synthetic_run_history_{len(non_box)}jobs.csv"
    )

    # Guarantee at least one clearly-HIGH and one unanimous-failure example
    # among whichever real jobs have the highest n_retrys, rather than
    # leaving the demonstrated risk range to chance -- see profile_for.
    forced = [
        {"fail_rate": 1.0,  "name": "always_failing"},
        {"fail_rate": 0.38, "name": "flaky"},
    ]

    out_rows = []
    profile_counts = {"healthy": 0, "occasional": 0, "flaky": 0, "always_failing": 0}

    for job in non_box:
        job_name = job["job_name"]
        machine = job["machine"] or random.choice(FALLBACK_MACHINES)
        prof = profile_for(job["n_retrys"], forced)
        profile_counts[prof["name"]] += 1
        per_day = runs_per_day(job_name)

        # A small, fixed per-job offset (seeded from the name, not fresh
        # randomness per row) so a job's runs land at a consistent time of
        # day across the window, like a real schedule would.
        base_hour = (hash(job_name) % 14) + 6  # 06:00-19:59
        base_minute = hash(job_name + "m") % 60

        for day in range(args.window_days):
            for slot in range(per_day):
                run_date = start + timedelta(days=day)
                hour = (base_hour + slot * (24 // max(per_day, 1))) % 24
                run_start = run_date.replace(hour=hour, minute=base_minute, second=0, microsecond=0)
                if run_start > now:
                    continue

                failed = random.random() < prof["fail_rate"]
                duration_s = random.randint(45, 1200)
                if failed:
                    duration_s = int(duration_s * random.uniform(0.3, 0.9))  # often aborts early
                run_end = run_start + timedelta(seconds=duration_s)

                attempt = 0
                if failed and random.random() < 0.4 and job["n_retrys"] > 0:
                    attempt = random.randint(1, min(job["n_retrys"], 2))

                out_rows.append({
                    "job_name": job_name,
                    "status": "FA" if failed else "SU",
                    "start_time": run_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end_time": run_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "exit_code": random.choice([1, 2, 127]) if failed else 0,
                    "machine": machine,
                    "attempt": attempt,
                })

    random.shuffle(out_rows)  # a real export isn't neatly grouped by job

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["job_name", "status", "start_time", "end_time",
                                          "exit_code", "machine", "attempt"])
        w.writeheader()
        w.writerows(out_rows)

    print(f"wrote {len(out_rows)} rows for {len(non_box)} non-BOX jobs "
         f"({len(jobs) - len(non_box)} BOX jobs excluded)")
    print(f"profile mix: {profile_counts}")
    print(f"window: {start.date()} .. {now.date()} ({args.window_days} days)")
    print(f"output: {out_path}")


if __name__ == "__main__":
    main()
