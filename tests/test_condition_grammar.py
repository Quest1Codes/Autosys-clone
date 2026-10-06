"""
Valid AutoSys conditions parse and evaluate; bad ones are flagged at import
(audit SEM-08 / PARSER-09).

These used to raise ConditionSyntaxError when the simulator evaluated them,
so the job waited forever, while the import reported them LOADED:
relational exit-code and global comparisons, unquoted global values, names
containing '#', job names spelled like a keyword, names starting with digits.
"""
from __future__ import annotations

import pytest

from autosys.parser.condition_parser import (
    ConditionSyntaxError, ExitCodeCondNode, JobCondNode, ValueCondNode, evaluate, parse_condition,
)


@pytest.mark.parametrize("cond, op, expected, code, ok", [
    ("e(a) > 4", ">", 4, 5, True),
    ("e(a) > 4", ">", 4, 4, False),
    ("exitcode(a) >= 4", ">=", 4, 4, True),
    ("e(a) < 1", "<", 1, 0, True),
    ("e(a) <= -1", "<=", -1, -1, True),
    ("e(a) <> 0", "!=", 0, 3, True),
])
def test_relational_exit_codes(cond, op, expected, code, ok):
    node = parse_condition(cond)
    assert isinstance(node, ExitCodeCondNode) and node.op == op and node.expected == expected
    assert evaluate(node, {}, job_exitcodes={"a": code}) is ok


def test_global_comparisons_numeric_and_unquoted():
    assert evaluate(parse_condition("v(COUNT) > 5"), {}, {"COUNT": "10"})      # 10 > 5, not "10" > "5"
    assert not evaluate(parse_condition("v(COUNT) > 5"), {}, {"COUNT": "3"})
    node = parse_condition("v(ENV) = PROD")
    assert isinstance(node, ValueCondNode) and node.expected == "PROD"
    assert evaluate(node, {}, {"ENV": "PROD"})


def test_equality_stays_textual():
    assert not evaluate(parse_condition('v(D) = "01"'), {}, {"D": "1"})


@pytest.mark.parametrize("name", ["job#1", "d", "s", "value", "v", "e", "OR", "and", "100_load"])
def test_unusual_job_names(name):
    node = parse_condition(f"s({name})")
    assert isinstance(node, JobCondNode) and node.job_name == name
    assert evaluate(node, {name: "SUCCESS"})


def test_keyword_named_jobs_in_a_compound_condition():
    node = parse_condition("s(d) & s(value) | f(OR)")
    assert evaluate(node, {"d": "SUCCESS", "value": "SUCCESS"})
    assert evaluate(node, {"OR": "FAILURE"})


def test_existing_forms_unchanged():
    assert parse_condition("success(a) and done(b)")
    assert parse_condition("s(a, 12.00) | n(b^PRD)")
    assert parse_condition('value(X) != "Y"')
    with pytest.raises(ConditionSyntaxError):
        parse_condition("s(a) && (")


def test_unparseable_condition_is_flagged_at_import():
    from autosys.parser.jil_apply import _check_conditions
    issues: list = []
    _check_conditions({"condition": "s(a) && (", "box_success": "s(b)"}, issues, 3)
    assert [i["code"] for i in issues] == ["condition_unparsed"]
    assert "condition" in issues[0]["message"]


def test_rename_handles_keyword_named_jobs():
    from autosys.analysis.condition_refs import rename_job_refs
    assert rename_job_refs("s(d) & s(dd)", "d", "x") == "s(x) & s(dd)"
