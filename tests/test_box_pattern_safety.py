"""
The assessment `box` filter is a SQL LIKE pattern, not a regex (audit SEC-02).

It used to be turned into a regex without escaping, so any viewer token --
including Shinro's service credential -- could send `(%%)*!` and freeze the
whole API process (catastrophic backtracking: 17 s on a 16-character box
name, growing ~10x per two characters), and `(` returned HTTP 500.
"""
from __future__ import annotations

import time

import pytest

from autosys.analysis.complexity import build_report
from autosys.db.schema import JobRow

ROWS = [JobRow(job_name="FID_PAY_DAILY_SETTLE_BOX_0001", job_type="BOX"),
        JobRow(job_name="fid_pay_daily_settle_box_0002", job_type="BOX"),
        JobRow(job_name="GL_POST_BOX", job_type="BOX"),
        JobRow(job_name="gl_child", job_type="CMD", box_name="GL_POST_BOX", command="x")]


@pytest.mark.parametrize("hostile", ["(%%)*!", "(a+)+$", "(.*){30}", "(", ")", "[", "\\", "*"])
def test_hostile_patterns_are_literal_and_fast(hostile):
    start = time.perf_counter()
    assert build_report(ROWS, box_pattern=hostile) == []
    assert time.perf_counter() - start < 1.0


@pytest.mark.parametrize("pattern, expected", [
    ("FID_PAY%", {"FID_PAY_DAILY_SETTLE_BOX_0001", "fid_pay_daily_settle_box_0002"}),
    ("%SETTLE%", {"FID_PAY_DAILY_SETTLE_BOX_0001", "fid_pay_daily_settle_box_0002"}),
    ("GL_POST_BOX", {"GL_POST_BOX", "gl_child"}),
    ("gl.post_box", set()),            # "." is literal, not "any character"
])
def test_like_semantics_unchanged(pattern, expected):
    assert {a.job_name for a in build_report(ROWS, box_pattern=pattern)} == expected


def test_api_rejects_overlong_box_filter():
    from autosys.app_server.routers.assessment import get_report
    import inspect
    default = inspect.signature(get_report).parameters["box"].default
    assert getattr(default, "max_length", None) is not None or any(
        getattr(m, "max_length", None) for m in getattr(default, "metadata", []))
