"""
The migration report's Airflow mapping is correct for Airflow 3
(audit SEM-14, SEM-15).

SEM-14  sla / sla_miss_callback were recommended (removed in Airflow 3.0);
        "convert the schedule to UTC" drifts an hour at every DST change;
        retry_delay_exponential / retry_count are not Airflow parameters;
        cross-instance events were "no native equivalent"; date_conditions
        alone (a plain cron schedule) was tagged as a complex calendar.
SEM-15  constructs with no direct Airflow equivalent were not tagged at all:
        look-back, exit-code, global-value and not-running conditions,
        box_success/box_failure, terminators.
"""
from __future__ import annotations

import pytest

from autosys.analysis.complexity import JobAssessment, astronomer_mapping, risk_mitigation
from autosys.analysis.gap_analysis import GAP_CATALOGUE, compute_gap_tags
from autosys.db.schema import JobRow


def _tags(**kw):
    kw.setdefault("job_name", "j")
    kw.setdefault("job_type", "CMD")
    return compute_gap_tags(JobRow(**kw))


@pytest.mark.parametrize("condition, tag", [
    ("s(a, 12.00)", "look-back-condition"),
    ("e(a) > 4", "exit-code-condition"),
    ('v(READY) = "Y"', "global-value-condition"),
    ("n(other)", "notrunning-condition"),
    ("s(a^PRD)", "cross-instance-event"),
])
def test_condition_constructs_are_tagged(condition, tag):
    assert tag in _tags(condition=condition)


def test_cross_instance_comes_from_the_parsed_reference_not_any_caret():
    assert "cross-instance-event" not in _tags(condition='v(X) = "a^b"')


def test_box_rules_and_terminators_are_tagged():
    assert "box-success-failure" in _tags(job_type="BOX", box_success="s(a) & s(b)")
    assert "terminator" in _tags(box_terminator=True)


def test_date_conditions_alone_is_not_a_complex_calendar():
    assert "complex-calendar" not in _tags(date_conditions=True)
    assert "complex-calendar" in _tags(date_conditions=True, run_calendar="HOLIDAYS")


def test_every_tag_has_a_severity_and_text():
    for tag, (sev, text) in GAP_CATALOGUE.items():
        assert sev in ("RED", "YELLOW", "GREEN") and text, tag


def test_no_airflow2_only_advice():
    text = " ".join(t for _, t in GAP_CATALOGUE.values())
    assert "Deadline Alerts" in GAP_CATALOGUE["sla-management"][1]
    assert "removed" in GAP_CATALOGUE["sla-management"][1]
    assert "no native" not in GAP_CATALOGUE["cross-instance-event"][1].lower()
    assert "maps to Airflow's sla parameter" not in text


def test_job_recommendations_use_airflow3_terms():
    a = JobAssessment(job_name="j", job_type="CMD", box_name="", size="M", effort_h=4,
                      drivers="", risk="HIGH", risk_drivers="failure rate 40%; retry rate 30%",
                      timezone="America/New_York", has_cross_box_dep=True)
    rec = astronomer_mapping(a) + " " + risk_mitigation(a)
    assert "retry_exponential_backoff" in rec and "retry_delay_exponential" not in rec
    assert "retries and retry_delay" in rec and "retry_count" not in rec
    assert "to UTC" not in rec.replace("converted to UTC", "") and "America/New_York" in rec
    assert "Asset" in rec
