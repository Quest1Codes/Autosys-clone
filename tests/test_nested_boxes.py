"""
Nested boxes in the assessment (audit SEM-03).

build_report only scoped chain depth for top-level boxes and their direct
children; anything deeper fell through to `orphans` and scored depth 1, so a
chain inside a nested box looked trivially parallel. Nested boxes are common
in large estates.
"""
from __future__ import annotations

import pytest

from autosys.analysis.complexity import build_report
from autosys.analysis.operational_risk import RunStats
from autosys.db.schema import JobRow


def _nested_chain(depth_of_nesting: int) -> list[JobRow]:
    rows, parent = [], None
    for level in range(depth_of_nesting):
        name = f"BOX{level}"
        rows.append(JobRow(job_name=name, job_type="BOX", box_name=parent))
        parent = name
    for i in range(8):
        rows.append(JobRow(job_name=f"step{i}", job_type="CMD", box_name=parent, command="x",
                           condition=f"s(step{i - 1})" if i else None))
    return rows


@pytest.mark.parametrize("levels", [1, 2, 3, 5])
def test_chain_depth_is_the_same_at_any_nesting_level(levels):
    res = {a.job_name: a for a in build_report(_nested_chain(levels))}
    assert [res[f"step{i}"].wave_depth for i in range(8)] == list(range(1, 9))


def test_nested_children_follow_their_box_in_report_order():
    order = [a.job_name for a in build_report(_nested_chain(3))]
    assert order[:4] == ["BOX0", "BOX1", "BOX2", "step0"]


def test_true_orphans_still_score_depth_one():
    # A child whose box is filtered out by box_pattern has no context.
    rows = [JobRow(job_name="keep_box", job_type="BOX"),
            JobRow(job_name="other_box", job_type="BOX"),
            JobRow(job_name="k1", job_type="CMD", box_name="keep_box", command="x"),
            JobRow(job_name="o1", job_type="CMD", box_name="other_box", command="x"),
            JobRow(job_name="o2", job_type="CMD", box_name="other_box", command="x",
                   condition="s(o1)")]
    res = {a.job_name: a for a in build_report(rows, box_pattern="keep%")}
    assert set(res) == {"keep_box", "k1"}


@pytest.mark.parametrize("outer, inner", [("A_outer", "Z_inner"), ("Z_outer", "A_inner")])
def test_box_risk_rolls_up_through_nested_boxes_regardless_of_names(outer, inner):
    rows = [JobRow(job_name=outer, job_type="BOX"),
            JobRow(job_name=inner, job_type="BOX", box_name=outer),
            JobRow(job_name="leaf", job_type="CMD", box_name=inner, command="x")]
    res = {a.job_name: a for a in build_report(
        rows, run_stats={"leaf": RunStats(total_runs=10, failures=6)})}
    assert res[outer].risk == res["leaf"].risk != "NO_DATA"
    assert res[outer].risk_drivers.startswith(f"inherited from child {inner}: inherited from child leaf")
