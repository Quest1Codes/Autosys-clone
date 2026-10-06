"""
Schedule values AutoSys would reject are flagged at import (audit PARSER-18).
"""
from __future__ import annotations

import pytest

from autosys.parser.jil_apply import _check_values


def _codes(**attrs):
    issues: list = []
    _check_values(attrs, issues, 1)
    return [i["message"].split(":")[0] for i in issues]


@pytest.mark.parametrize("attrs, flagged", [
    ({"start_times": '"25:00"'}, ["start_times"]),
    ({"start_times": '"10:00, 10:61"'}, ["start_times"]),
    ({"start_mins": "0,75"}, ["start_mins"]),
    ({"run_window": '"bogus"'}, ["run_window"]),
    ({"n_retrys": "25"}, ["n_retrys"]),
    ({"n_retrys": "x"}, ["n_retrys"]),
])
def test_bad_values_are_flagged(attrs, flagged):
    assert _codes(**attrs) == flagged


def test_good_values_pass():
    assert _codes(start_times='"6:00, 23:59"', start_mins="0,15,30,45",
                  run_window='"22:00 - 02:00"', n_retrys="20") == []


def test_job_still_loads_with_a_warning():
    from autosys.parser.jil_apply import apply_operation
    from autosys.parser.jil_parser import JILParser
    op = JILParser().parse_text('insert_job: a job_type: CMD\ncommand: x\nmachine: m\n'
                                'start_times: "25:00"\n', tolerant=True)[0]
    res = apply_operation(None, op, dry_run=True)
    assert res.action == "OK"
    assert any(i["code"] == "invalid_value" for i in res.issues)
