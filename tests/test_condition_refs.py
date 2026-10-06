"""
One definition of "which jobs does this condition depend on" (audit SEM-01/02).

autorep writes conditions in short form -- s(job), f(job) -- and the
assessment's dependency regexes only knew the long form, so on a real export
chain depth, blast radius and cross-box counts collapsed to near zero (the
same 8-job chain scored 206 h as success() and 34 h as s()). Three separate
regexes also disagreed with each other. Every caller now goes through
autosys.analysis.condition_refs.condition_job_refs.
"""
from __future__ import annotations

import pytest

from autosys.analysis.condition_refs import condition_job_refs
from autosys.analysis.complexity import build_report
from autosys.analysis.dependency_graph import fan_in_counts, referenced_jobs
from autosys.db.schema import JobRow


@pytest.mark.parametrize("condition, expected", [
    # long and short forms of every predicate
    ("success(a)", {"a"}), ("s(a)", {"a"}),
    ("failure(a)", {"a"}), ("f(a)", {"a"}),
    ("done(a)", {"a"}), ("d(a)", {"a"}),
    ("terminated(a)", {"a"}), ("t(a)", {"a"}),
    ("notrunning(a)", {"a"}), ("n(a)", {"a"}),
    ("exitcode(a) = 0", {"a"}), ("e(a) = 0", {"a"}), ("e(a)!=1", {"a"}),
    # combinations, word operators, whitespace, look-back
    ("s(a) & f(b) | d(c)", {"a", "b", "c"}),
    ("success(a) and failure(b) OR done(c)", {"a", "b", "c"}),
    ("s( a ) & s (b)", {"a", "b"}),
    ("success(a, 12.00) & s(b,0)", {"a", "b"}),
    ("(s(a) | s(b)) & (s(c) | e(d) = 0)", {"a", "b", "c", "d"}),
    # realistic names: dots, dashes, underscores
    ("s(PAY.daily-extract_01) & f(GL.post)", {"PAY.daily-extract_01", "GL.post"}),
    # cross-instance references are another scheduler's job, not a local edge
    ("s(a^PRD)", set()), ("s(a^PRD) & s(b)", {"b"}), ("e(x^P01) = 0", set()),
    # global-variable conditions reference no job
    ('v(READY) = "Y"', set()), ('value(READY) = "Y" & s(a)', {"a"}),
    # names the strict parser rejects still resolve via the tolerant fallback
    ("s(pay#run)", {"pay#run"}), ("s(job#1) & f(job#2)", {"job#1", "job#2"}),
    ("e(a) > 4", {"a"}),
    # empty
    (None, set()), ("", set()), ("   ", set()),
])
def test_condition_job_refs(condition, expected):
    assert condition_job_refs(condition) == expected


def test_referenced_jobs_respects_scope():
    assert referenced_jobs("s(a) & s(outside)", {"a", "b"}) == {"a"}


def _chain(prefix: str, fn: str) -> list[JobRow]:
    rows = [JobRow(job_name=f"{prefix}box", job_type="BOX")]
    for i in range(8):
        rows.append(JobRow(job_name=f"{prefix}{i}", job_type="CMD", box_name=f"{prefix}box",
                           command="x", condition=f"{fn}({prefix}{i - 1})" if i else None))
    return rows


def test_short_and_long_form_chains_score_identically():
    long_form = build_report(_chain("j", "success"))
    short_form = build_report(_chain("j", "s"))
    assert [(a.job_name, a.wave_depth, a.size, a.effort_h) for a in short_form] == \
           [(a.job_name, a.wave_depth, a.size, a.effort_h) for a in long_form]
    assert max(a.wave_depth for a in short_form) == 8


def test_blast_radius_counts_short_form_dependents():
    rows = {r.job_name: r for r in [
        JobRow(job_name="up", job_type="CMD"),
        JobRow(job_name="d1", job_type="CMD", condition="s(up)"),
        JobRow(job_name="d2", job_type="CMD", condition="e(up) = 0"),
        JobRow(job_name="d3", job_type="CMD", condition="success(up, 01.00)"),
    ]}
    assert fan_in_counts(rows)["up"] == 3


def test_cross_box_detects_short_form_and_look_back(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'xb.db'}")
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    from autosys.analysis.migration_signals import cross_box_dependencies
    connection.reset_engines()
    create_all_sync()
    with connection.sync_session() as s:
        for name, box, cond in [("boxA", None, None), ("boxB", None, None), ("boxC", None, "s(boxA)"),
                                ("a7", "boxA", None), ("b7", "boxB", None),
                                ("c0", "boxC", "s(a7) & success(b7, 12.00)")]:
            s.add(JobRow(job_name=name, job_type="BOX" if box is None else "CMD", box_name=box,
                         command=None if box is None else "x", condition=cond))
        s.commit()
        result = cross_box_dependencies(s)
    connection.reset_engines()
    pairs = {(x["job"], x["depends_on"]) for x in result["cross_box"]}
    assert ("c0", "a7") in pairs and ("c0", "b7") in pairs and ("boxC", "boxA") in pairs


def test_wcc_graph_uses_the_same_definition():
    from autosys.wcc.app import _extract_deps
    assert set(_extract_deps("s(a.b) & e(c) = 0 & s(x^PRD)")) == {"a.b", "c"}
    # "a(" is not an AutoSys predicate; the old WCC regex treated it as one
    assert _extract_deps("a(job1)") == []
