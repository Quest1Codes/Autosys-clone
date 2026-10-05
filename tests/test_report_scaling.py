"""
build_report must stay linear in estate size (task E6).

score_job's child count and build_report's BOX risk roll-up each rescanned
every row for every BOX, so the report was quadratic: ~32s at 8K jobs and
about an hour extrapolated to an 85K-job estate. Both now read one
box -> children index built up front.

The timing bound below is deliberately loose -- the fixed code does 40K jobs
in well under a second, the quadratic code took minutes -- so it catches a
regression to O(n^2) without being flaky on a slow CI runner.
"""
from __future__ import annotations

import time

from autosys.analysis.complexity import build_report, score_job
from autosys.analysis.operational_risk import RunStats
from autosys.db.schema import JobRow


def _box_heavy_estate(n_boxes: int, kids_per_box: int) -> list[JobRow]:
    rows = []
    for b in range(n_boxes):
        rows.append(JobRow(job_name=f"box{b:06d}", job_type="BOX"))
        for k in range(kids_per_box):
            rows.append(JobRow(job_name=f"box{b:06d}_j{k}", job_type="CMD",
                               box_name=f"box{b:06d}", command="run.sh"))
    return rows


def test_box_heavy_estate_scores_in_linear_time():
    # 20,000 boxes x 1 child = 40,000 jobs. Quadratic code: 20K boxes each
    # scanning 40K rows, three times over -- minutes. Linear code: <1s.
    rows = _box_heavy_estate(20_000, 1)
    start = time.perf_counter()
    results = build_report(rows)
    elapsed = time.perf_counter() - start
    assert len(results) == 40_000
    assert elapsed < 30, f"build_report took {elapsed:.1f}s on 40K jobs -- quadratic again?"


def test_child_count_matches_precomputed_and_rescan():
    rows = _box_heavy_estate(3, 17)  # 17 children trips the >15 XL signal
    all_rows = {r.job_name: r for r in rows}
    counts = {"box000000": 17, "box000001": 17, "box000002": 17}
    box = all_rows["box000001"]
    assert score_job(box, all_rows) == score_job(box, all_rows, child_counts=counts)
    size, drivers = score_job(box, all_rows, child_counts=counts)
    assert size == "XL"
    assert any("17 children" in d for d in drivers)


def test_box_inherits_worst_child_risk_from_unfiltered_set():
    # The roll-up reads children from the full estate, not the box_pattern
    # filtered rows, and keeps the first-seen child on a tie.
    rows = _box_heavy_estate(1, 3)
    risky = RunStats(total_runs=10, failures=6)
    stats = {"box000000_j1": risky, "box000000_j2": risky}
    by_name = {a.job_name: a for a in build_report(rows, run_stats=stats)}
    box = by_name["box000000"]
    assert box.risk == by_name["box000000_j1"].risk != "NO_DATA"
    assert "inherited from child box000000_j1" in box.risk_drivers
