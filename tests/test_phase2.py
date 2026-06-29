"""
Phase 2 test suite — JIL Parser pipeline.

Coverage
--------
1.  Lexer          — comment stripping, stanza header, attribute lines,
                     quoted values, multi-word command values
2.  JIL Parser     — single/multi-stanza, all job types, attribute coercion,
                     delete_job, unknown attributes forwarded cleanly
3.  Condition      — tokeniser, AST shapes, precedence, evaluate(), round-trip
                     condition_to_str(), list_job_dependencies()
4.  Variable sub   — builtins, user globals, strict/lenient mode, batch sub
5.  End-to-end     — parse the real demo_etl.jil from examples/ and assert
                     every job's attributes are correct
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import pytest

from autosys.parser.lexer import (
    Lexer, Token, TokenKind, LexError, strip_comments, tokenize,
)
from autosys.parser.jil_parser import (
    JILParser, JILOperation, JILParseError, parse_jil, jobs_from_jil, parse_jil_file,
)
from autosys.parser.condition_parser import (
    parse_condition, evaluate, condition_to_str, list_job_dependencies,
    JobCondNode, ValueCondNode, AndNode, OrNode, ConditionSyntaxError,
)
from autosys.parser.variable_sub import (
    substitute, build_builtins, substitute_job_attrs,
    list_variables, UndefinedVariableError,
)

# Path to the bundled demo JIL file
_DEMO_JIL = Path(__file__).parents[1] / "examples" / "demo_etl.jil"


# ===========================================================================
# 1. Lexer
# ===========================================================================

class TestStripComments:

    def test_removes_single_line_block_comment(self):
        result = strip_comments("/* hello */\ninsert_job: a   job_type: BOX")
        assert "hello" not in result
        assert "insert_job" in result

    def test_preserves_newlines_inside_comment(self):
        text = "/* line1\nline2 */\ninsert_job: a   job_type: BOX"
        result = strip_comments(text)
        # Two newlines inside comment must be preserved so line 3 stays line 3
        assert result.count("\n") == text.count("\n")

    def test_inline_comment_replaced_with_spaces(self):
        result = strip_comments("a: 1  /* inline */ b: 2")
        assert "/*" not in result
        assert "b: 2" in result


class TestLexer:

    def test_simple_box_stanza(self):
        jil = 'insert_job: my_box   job_type: BOX'
        tokens = tokenize(jil)
        kinds = [t.kind for t in tokens]
        assert TokenKind.DIRECTIVE  in kinds
        assert TokenKind.JOB_NAME   in kinds
        assert TokenKind.ATTR_NAME  in kinds
        assert TokenKind.VALUE      in kinds
        assert tokens[-1].kind == TokenKind.EOF

    def test_directive_value_is_insert_job(self):
        tokens = tokenize("insert_job: my_job   job_type: CMD")
        assert tokens[0] == Token(TokenKind.DIRECTIVE, "insert_job", 1)

    def test_job_name_token(self):
        tokens = tokenize("insert_job: extract_sales   job_type: CMD")
        assert tokens[1] == Token(TokenKind.JOB_NAME, "extract_sales", 1)

    def test_inline_attribute_on_header(self):
        tokens = tokenize("insert_job: my_job   job_type: CMD")
        attr_toks = [t for t in tokens if t.kind == TokenKind.ATTR_NAME]
        val_toks  = [t for t in tokens if t.kind == TokenKind.VALUE]
        assert attr_toks[0].value == "job_type"
        assert val_toks[0].value  == "CMD"

    def test_attribute_line_emits_two_tokens(self):
        tokens = tokenize("insert_job: j   job_type: BOX\nowner: svc_demo")
        # Find owner token
        owner_idx = next(i for i, t in enumerate(tokens) if t.value == "owner")
        assert tokens[owner_idx].kind     == TokenKind.ATTR_NAME
        assert tokens[owner_idx + 1].kind == TokenKind.VALUE
        assert tokens[owner_idx + 1].value == "svc_demo"

    def test_quoted_value_strips_quotes(self):
        tokens = tokenize('insert_job: j   job_type: BOX\nstart_times: "06:00"')
        st_val = next(t for t in tokens if t.kind == TokenKind.VALUE and ":" in t.value)
        assert st_val.value == "06:00"   # quotes stripped

    def test_command_value_preserves_spaces(self):
        cmd = "/scripts/extract.sh --date %%DATE%%"
        tokens = tokenize(f"insert_job: j   job_type: CMD\ncommand: {cmd}\nmachine: localhost")
        cmd_val = next(t for t in tokens if t.kind == TokenKind.VALUE and "/scripts" in t.value)
        assert cmd_val.value == cmd

    def test_condition_value_is_whole_expression(self):
        expr = "success(check_source_ready)"
        tokens = tokenize(f"insert_job: j   job_type: CMD\ncondition: {expr}\nmachine: m\ncommand: x")
        cond_val = next(t for t in tokens if t.kind == TokenKind.VALUE and "success" in t.value)
        assert cond_val.value == expr

    def test_days_of_week_comma_list_is_single_value(self):
        tokens = tokenize("insert_job: j   job_type: BOX\ndays_of_week: mo,tu,we,th,fr")
        dow_val = next(t for t in tokens if t.kind == TokenKind.VALUE and "mo" in t.value)
        assert dow_val.value == "mo,tu,we,th,fr"

    def test_line_numbers_are_tracked(self):
        jil = "insert_job: j   job_type: BOX\nowner: svc_demo"
        tokens = tokenize(jil)
        owner_tok = next(t for t in tokens if t.value == "owner")
        assert owner_tok.line == 2

    def test_eof_is_last_token(self):
        tokens = tokenize("insert_job: j   job_type: BOX")
        assert tokens[-1].kind == TokenKind.EOF

    def test_comment_only_lines_produce_no_tokens(self):
        tokens = tokenize("/* just a comment */")
        # Only EOF should be produced
        assert all(t.kind == TokenKind.EOF for t in tokens)

    def test_multiple_stanzas(self):
        jil = (
            "insert_job: box1   job_type: BOX\n"
            "owner: svc_demo\n\n"
            "insert_job: job1   job_type: CMD\n"
            "command: echo hi\n"
            "machine: localhost\n"
        )
        tokens = tokenize(jil)
        directives = [t for t in tokens if t.kind == TokenKind.DIRECTIVE]
        assert len(directives) == 2

    def test_update_job_directive(self):
        tokens = tokenize("update_job: my_job\nalarm_if_fail: 1")
        assert tokens[0] == Token(TokenKind.DIRECTIVE, "update_job", 1)

    def test_delete_job_directive(self):
        tokens = tokenize("delete_job: my_job")
        assert tokens[0].kind  == TokenKind.DIRECTIVE
        assert tokens[0].value == "delete_job"


# ===========================================================================
# 2. JIL Parser
# ===========================================================================

class TestJILParser:

    def test_parse_box_job(self):
        jil = (
            "insert_job: demo_etl_box   job_type: BOX\n"
            "owner: svc_demo\n"
            "start_times: \"06:00\"\n"
            "days_of_week: mo,tu,we,th,fr\n"
            "alarm_if_fail: 1\n"
            "max_run_alarm: 120\n"
        )
        ops = parse_jil(jil)
        assert len(ops) == 1
        op = ops[0]
        assert op.op == "insert"
        assert op.job.job_name == "demo_etl_box"
        assert op.job.job_type == "BOX"
        assert op.job.start_times == ["06:00"]
        assert "mo" in op.job.days_of_week
        assert op.job.alarm_if_fail is True
        assert op.job.max_run_alarm == 120

    def test_parse_cmd_job(self):
        jil = (
            "insert_job: extract_sales   job_type: CMD\n"
            "box_name: demo_etl_box\n"
            "command: /scripts/extract.sh --date %%DATE%%\n"
            "machine: etl-server-01\n"
            "condition: success(check_source_ready)\n"
            "n_retrys: 2\n"
            "alarm_if_fail: 1\n"
        )
        ops = parse_jil(jil)
        job = ops[0].job
        assert job.job_type  == "CMD"
        assert job.command   == "/scripts/extract.sh --date %%DATE%%"
        assert job.machine   == "etl-server-01"
        assert job.box_name  == "demo_etl_box"
        assert job.condition == "success(check_source_ready)"
        assert job.n_retrys  == 2
        assert job.alarm_if_fail is True

    def test_parse_multiple_stanzas(self):
        jil = (
            "insert_job: box1   job_type: BOX\n"
            "owner: svc_demo\n\n"
            "insert_job: job1   job_type: CMD\n"
            "command: echo hi\n"
            "machine: localhost\n"
        )
        ops = parse_jil(jil)
        assert len(ops) == 2
        assert ops[0].job.job_name == "box1"
        assert ops[1].job.job_name == "job1"

    def test_delete_job_produces_partial_model(self):
        ops = parse_jil("delete_job: old_job")
        assert ops[0].op == "delete"
        assert ops[0].job.job_name == "old_job"

    def test_update_job_op_code(self):
        ops = parse_jil("update_job: my_job\nalarm_if_fail: 1\nmachine: m\ncommand: x")
        assert ops[0].op == "update"

    def test_bool_coercion_alarm_if_fail(self):
        ops = parse_jil("insert_job: j   job_type: BOX\nalarm_if_fail: 1")
        assert ops[0].job.alarm_if_fail is True

    def test_bool_coercion_zero_is_false(self):
        ops = parse_jil("insert_job: j   job_type: BOX\nalarm_if_fail: 0")
        assert ops[0].job.alarm_if_fail is False

    def test_int_coercion_n_retrys(self):
        ops = parse_jil(
            "insert_job: j   job_type: CMD\n"
            "command: x\nmachine: m\nn_retrys: 3"
        )
        assert ops[0].job.n_retrys == 3

    def test_raw_attrs_preserved(self):
        ops = parse_jil(
            "insert_job: j   job_type: CMD\ncommand: x\nmachine: m\nn_retrys: 3"
        )
        assert ops[0].raw_attrs["n_retrys"] == "3"   # raw string

    def test_days_of_week_all_expands(self):
        ops = parse_jil("insert_job: j   job_type: BOX\ndays_of_week: all")
        assert len(ops[0].job.days_of_week) == 7

    def test_days_of_week_weekdays(self):
        ops = parse_jil("insert_job: j   job_type: BOX\ndays_of_week: mo,tu,we,th,fr")
        assert ops[0].job.days_of_week == ["mo", "tu", "we", "th", "fr"]

    def test_source_line_is_recorded(self):
        jil = "\n\ninsert_job: j   job_type: BOX\n"
        ops = parse_jil(jil)
        assert ops[0].source_line == 3

    def test_jobs_from_jil_shortcut(self):
        jil = "insert_job: j   job_type: BOX\nowner: demo"
        jobs = jobs_from_jil(jil)
        assert len(jobs) == 1
        assert jobs[0].job_name == "j"

    def test_parse_jil_file(self, tmp_path):
        jil_file = tmp_path / "test.jil"
        jil_file.write_text(
            "insert_job: file_job   job_type: BOX\nowner: svc_demo\n"
        )
        ops = parse_jil_file(str(jil_file))
        assert ops[0].job.job_name == "file_job"

    def test_parallel_jobs_share_box(self):
        jil = (
            "insert_job: gen_report   job_type: CMD\n"
            "box_name: demo_etl_box\n"
            "command: /scripts/gen_report.py\n"
            "machine: etl-server-01\n"
            "condition: success(extract_sales)\n\n"
            "insert_job: load_to_warehouse   job_type: CMD\n"
            "box_name: demo_etl_box\n"
            "command: /scripts/load_snowflake.sh\n"
            "machine: etl-server-02\n"
            "condition: success(extract_sales)\n"
        )
        ops = parse_jil(jil)
        assert len(ops) == 2
        assert all(op.job.box_name == "demo_etl_box" for op in ops)
        assert all(op.job.condition == "success(extract_sales)" for op in ops)


# ===========================================================================
# 3. Condition parser
# ===========================================================================

class TestConditionAST:

    def test_simple_success(self):
        node = parse_condition("success(extract_sales)")
        assert isinstance(node, JobCondNode)
        assert node.func     == "success"
        assert node.job_name == "extract_sales"

    def test_simple_failure(self):
        node = parse_condition("failure(bad_job)")
        assert isinstance(node, JobCondNode)
        assert node.func == "failure"

    def test_done_predicate(self):
        node = parse_condition("done(any_job)")
        assert node.func == "done"

    def test_notrunning_predicate(self):
        node = parse_condition("notrunning(long_job)")
        assert node.func == "notrunning"

    def test_terminated_predicate(self):
        node = parse_condition("terminated(killed_job)")
        assert node.func == "terminated"

    def test_activated_predicate(self):
        node = parse_condition("activated(my_box)")
        assert node.func == "activated"

    def test_and_expression(self):
        node = parse_condition("success(a) & success(b)")
        assert isinstance(node, AndNode)
        assert isinstance(node.left,  JobCondNode)
        assert isinstance(node.right, JobCondNode)

    def test_or_expression(self):
        node = parse_condition("success(a) | failure(b)")
        assert isinstance(node, OrNode)

    def test_and_has_higher_precedence_than_or(self):
        # success(a) | success(b) & success(c)
        # should be:  success(a) | (success(b) & success(c))
        node = parse_condition("success(a) | success(b) & success(c)")
        assert isinstance(node, OrNode)
        assert isinstance(node.left,  JobCondNode)
        assert isinstance(node.right, AndNode)      # & binds tighter

    def test_parentheses_override_precedence(self):
        # (success(a) | success(b)) & success(c)
        node = parse_condition("(success(a) | success(b)) & success(c)")
        assert isinstance(node, AndNode)
        assert isinstance(node.left, OrNode)
        assert isinstance(node.right, JobCondNode)

    def test_value_condition_equals(self):
        node = parse_condition('value(BATCH_DATE) = "20260625"')
        assert isinstance(node, ValueCondNode)
        assert node.global_name == "BATCH_DATE"
        assert node.op          == "="
        assert node.expected    == "20260625"

    def test_value_condition_not_equals(self):
        node = parse_condition('value(ENV) != "PROD"')
        assert isinstance(node, ValueCondNode)
        assert node.op == "!="

    def test_complex_condition(self):
        expr = "success(generate_report) & success(load_to_warehouse)"
        node = parse_condition(expr)
        assert isinstance(node, AndNode)

    def test_nested_parens(self):
        expr = "success(a) & (success(b) | failure(c))"
        node = parse_condition(expr)
        assert isinstance(node, AndNode)
        assert isinstance(node.right, OrNode)

    def test_syntax_error_raises(self):
        with pytest.raises(ConditionSyntaxError):
            parse_condition("success(a) &&")   # double operator

    def test_syntax_error_empty_parens(self):
        with pytest.raises(ConditionSyntaxError):
            parse_condition("success()")  # empty job name is an IDENT issue → parse error


class TestConditionEvaluate:

    def test_success_true_when_job_succeeded(self):
        node = parse_condition("success(a)")
        assert evaluate(node, {"a": "SUCCESS"}) is True

    def test_success_false_when_job_running(self):
        node = parse_condition("success(a)")
        assert evaluate(node, {"a": "RUNNING"}) is False

    def test_failure_true_when_job_failed(self):
        node = parse_condition("failure(a)")
        assert evaluate(node, {"a": "FAILURE"}) is True

    def test_done_true_for_success(self):
        node = parse_condition("done(a)")
        assert evaluate(node, {"a": "SUCCESS"}) is True

    def test_done_true_for_failure(self):
        node = parse_condition("done(a)")
        assert evaluate(node, {"a": "FAILURE"}) is True

    def test_done_false_for_running(self):
        node = parse_condition("done(a)")
        assert evaluate(node, {"a": "RUNNING"}) is False

    def test_notrunning_true_for_inactive(self):
        node = parse_condition("notrunning(a)")
        assert evaluate(node, {"a": "INACTIVE"}) is True

    def test_notrunning_false_for_running(self):
        node = parse_condition("notrunning(a)")
        assert evaluate(node, {"a": "RUNNING"}) is False

    def test_and_both_true(self):
        node = parse_condition("success(a) & success(b)")
        assert evaluate(node, {"a": "SUCCESS", "b": "SUCCESS"}) is True

    def test_and_one_false(self):
        node = parse_condition("success(a) & success(b)")
        assert evaluate(node, {"a": "SUCCESS", "b": "FAILURE"}) is False

    def test_or_one_true(self):
        node = parse_condition("success(a) | success(b)")
        assert evaluate(node, {"a": "SUCCESS", "b": "FAILURE"}) is True

    def test_or_both_false(self):
        node = parse_condition("success(a) | success(b)")
        assert evaluate(node, {"a": "FAILURE", "b": "FAILURE"}) is False

    def test_missing_job_treated_as_inactive(self):
        node = parse_condition("success(missing_job)")
        assert evaluate(node, {}) is False

    def test_value_condition_equals_true(self):
        node = parse_condition('value(MY_VAR) = "hello"')
        assert evaluate(node, {}, {"MY_VAR": "hello"}) is True

    def test_value_condition_equals_false(self):
        node = parse_condition('value(MY_VAR) = "hello"')
        assert evaluate(node, {}, {"MY_VAR": "world"}) is False

    def test_value_condition_not_equals(self):
        node = parse_condition('value(ENV) != "PROD"')
        assert evaluate(node, {}, {"ENV": "DEV"}) is True

    def test_complex_fan_in_all_success(self):
        expr = "success(generate_report) & success(load_to_warehouse)"
        node = parse_condition(expr)
        statuses = {"generate_report": "SUCCESS", "load_to_warehouse": "SUCCESS"}
        assert evaluate(node, statuses) is True

    def test_complex_fan_in_one_not_done(self):
        expr = "success(generate_report) & success(load_to_warehouse)"
        node = parse_condition(expr)
        statuses = {"generate_report": "SUCCESS", "load_to_warehouse": "RUNNING"}
        assert evaluate(node, statuses) is False


class TestConditionHelpers:

    def test_condition_to_str_simple(self):
        node = parse_condition("success(a)")
        assert condition_to_str(node) == "success(a)"

    def test_condition_to_str_and(self):
        node = parse_condition("success(a) & success(b)")
        assert condition_to_str(node) == "success(a) & success(b)"

    def test_condition_to_str_or(self):
        node = parse_condition("success(a) | failure(b)")
        assert condition_to_str(node) == "success(a) | failure(b)"

    def test_condition_to_str_value(self):
        node = parse_condition('value(MY_VAR) = "x"')
        assert condition_to_str(node) == 'value(MY_VAR) = "x"'

    def test_list_job_dependencies_simple(self):
        node = parse_condition("success(extract_sales)")
        assert list_job_dependencies(node) == ["extract_sales"]

    def test_list_job_dependencies_deduplicates(self):
        node = parse_condition("success(a) & (success(b) | success(a))")
        deps = list_job_dependencies(node)
        assert deps == ["a", "b"]   # order: depth-first, a appears first

    def test_list_job_dependencies_value_node_not_included(self):
        node = parse_condition('value(MY_VAR) = "x"')
        assert list_job_dependencies(node) == []


# ===========================================================================
# 4. Variable substitution
# ===========================================================================

class TestBuildBuiltins:

    def test_date_format(self):
        b = build_builtins(run_date=date(2026, 6, 25))
        assert b["DATE"] == "06252026"

    def test_yyyy_mm_dd(self):
        b = build_builtins(run_date=date(2026, 6, 25))
        assert b["YYYY"] == "2026"
        assert b["MM"]   == "06"
        assert b["DD"]   == "25"

    def test_time_format(self):
        b = build_builtins(dispatch_time=datetime(2026, 6, 25, 8, 5))
        assert b["TIME"] == "0805"

    def test_autorun_y(self):
        b = build_builtins(autorun=True)
        assert b["AUTORUN"] == "Y"

    def test_autorun_n(self):
        b = build_builtins(autorun=False)
        assert b["AUTORUN"] == "N"


class TestSubstitute:

    def test_date_substitution(self):
        result = substitute("/data/%%DATE%%.csv", run_date=date(2026, 6, 25))
        assert result == "/data/06252026.csv"

    def test_yyyy_mm_dd_substitution(self):
        result = substitute(
            "/data/%%YYYY%%/%%MM%%/%%DD%%/file.csv",
            run_date=date(2026, 6, 25),
        )
        assert result == "/data/2026/06/25/file.csv"

    def test_user_global_substitution(self):
        result = substitute("Hello %%NAME%%", globals={"NAME": "World"})
        assert result == "Hello World"

    def test_global_name_case_insensitive(self):
        result = substitute("%%my_var%%", globals={"MY_VAR": "ok"})
        assert result == "ok"

    def test_strict_raises_on_undefined(self):
        with pytest.raises(UndefinedVariableError):
            substitute("%%UNDEFINED%%", strict=True)

    def test_lenient_leaves_token_unchanged(self):
        result = substitute("%%UNDEFINED%%", strict=False)
        assert result == "%%UNDEFINED%%"

    def test_no_tokens_returns_unchanged(self):
        s = "/scripts/static_script.sh"
        assert substitute(s) == s

    def test_multiple_tokens_in_one_string(self):
        result = substitute(
            "/scripts/extract.sh --date %%DATE%% --env %%ENV%%",
            globals={"ENV": "PROD"},
            run_date=date(2026, 6, 25),
        )
        assert result == "/scripts/extract.sh --date 06252026 --env PROD"

    def test_list_variables(self):
        template = "/scripts/load.sh --date %%DATE%% --env %%ENV%%"
        assert list_variables(template) == ["DATE", "ENV"]

    def test_list_variables_deduplicates(self):
        template = "%%A%% + %%B%% + %%A%%"
        assert list_variables(template) == ["A", "B"]

    def test_substitute_job_attrs_expands_command(self):
        attrs = {
            "command": "/scripts/extract.sh --date %%DATE%%",
            "std_out_file": "/logs/%%YYYY%%/extract.out",
            "machine": "etl-server-01",   # NOT expandable — should stay as-is
        }
        result = substitute_job_attrs(attrs, run_date=date(2026, 6, 25))
        assert result["command"]      == "/scripts/extract.sh --date 06252026"
        assert result["std_out_file"] == "/logs/2026/extract.out"
        assert result["machine"]      == "etl-server-01"  # unchanged

    def test_substitute_job_attrs_does_not_mutate_input(self):
        attrs = {"command": "/scripts/extract.sh %%DATE%%"}
        _ = substitute_job_attrs(attrs, run_date=date(2026, 6, 25))
        assert attrs["command"] == "/scripts/extract.sh %%DATE%%"  # original unchanged


# ===========================================================================
# 5. End-to-end: parse the real demo_etl.jil
# ===========================================================================

class TestDemoEtlJIL:
    """
    Parse the actual demo_etl.jil from examples/ and assert every attribute
    of every job matches what the file says.

    This is the most important test in Phase 2 — it proves the full pipeline
    works on a realistic real-world JIL file.
    """

    @pytest.fixture(scope="class")
    def ops(self) -> list[JILOperation]:
        assert _DEMO_JIL.exists(), f"Demo JIL not found at {_DEMO_JIL}"
        return parse_jil_file(str(_DEMO_JIL))

    @pytest.fixture(scope="class")
    def jobs_by_name(self, ops) -> dict:
        return {op.job.job_name: op.job for op in ops}

    # --- Job count ---

    def test_parses_7_jobs(self, ops):
        assert len(ops) == 7

    def test_all_ops_are_insert(self, ops):
        assert all(op.op == "insert" for op in ops)

    # --- BOX job ---

    def test_box_job_present(self, jobs_by_name):
        assert "demo_etl_box" in jobs_by_name

    def test_box_job_type(self, jobs_by_name):
        assert jobs_by_name["demo_etl_box"].job_type == "BOX"

    def test_box_start_time(self, jobs_by_name):
        assert "06:00" in jobs_by_name["demo_etl_box"].start_times

    def test_box_days_of_week(self, jobs_by_name):
        box = jobs_by_name["demo_etl_box"]
        assert set(box.days_of_week) == {"mo", "tu", "we", "th", "fr"}

    def test_box_exclude_calendar(self, jobs_by_name):
        assert jobs_by_name["demo_etl_box"].exclude_calendar == "us_holidays"

    def test_box_alarm_if_fail(self, jobs_by_name):
        assert jobs_by_name["demo_etl_box"].alarm_if_fail is True

    def test_box_max_run_alarm(self, jobs_by_name):
        assert jobs_by_name["demo_etl_box"].max_run_alarm == 120

    # --- check_source_ready ---

    def test_check_source_ready_type(self, jobs_by_name):
        assert jobs_by_name["check_source_ready"].job_type == "CMD"

    def test_check_source_ready_box(self, jobs_by_name):
        assert jobs_by_name["check_source_ready"].box_name == "demo_etl_box"

    def test_check_source_ready_command_has_date_var(self, jobs_by_name):
        cmd = jobs_by_name["check_source_ready"].command
        assert "%%DATE%%" in cmd

    def test_check_source_ready_machine(self, jobs_by_name):
        assert jobs_by_name["check_source_ready"].machine == "etl-server-01"

    def test_check_source_ready_n_retrys(self, jobs_by_name):
        assert jobs_by_name["check_source_ready"].n_retrys == 2

    # --- extract_sales has a condition ---

    def test_extract_sales_condition(self, jobs_by_name):
        job = jobs_by_name["extract_sales"]
        assert job.condition == "success(check_source_ready)"

    # --- Parallel fan-out jobs both depend on extract_sales ---

    def test_generate_report_condition(self, jobs_by_name):
        assert jobs_by_name["generate_report"].condition == "success(extract_sales)"

    def test_load_to_warehouse_condition(self, jobs_by_name):
        assert jobs_by_name["load_to_warehouse"].condition == "success(extract_sales)"

    def test_load_to_warehouse_different_machine(self, jobs_by_name):
        # Proves the parser reads machine per-job, not globally
        assert jobs_by_name["load_to_warehouse"].machine == "etl-server-02"

    # --- Fan-in: send_success_email waits for both parallel jobs ---

    def test_send_success_email_condition(self, jobs_by_name):
        job = jobs_by_name["send_success_email"]
        assert "generate_report" in job.condition
        assert "load_to_warehouse" in job.condition

    def test_send_success_email_alarm_off(self, jobs_by_name):
        assert jobs_by_name["send_success_email"].alarm_if_fail is False

    # --- Standalone nightly_cleanup job ---

    def test_nightly_cleanup_present(self, jobs_by_name):
        assert "nightly_cleanup" in jobs_by_name

    def test_nightly_cleanup_has_no_box(self, jobs_by_name):
        assert jobs_by_name["nightly_cleanup"].box_name is None

    def test_nightly_cleanup_runs_all_days(self, jobs_by_name):
        job = jobs_by_name["nightly_cleanup"]
        assert len(job.days_of_week) == 7   # "all" expanded

    def test_nightly_cleanup_start_time(self, jobs_by_name):
        assert "02:00" in jobs_by_name["nightly_cleanup"].start_times

    # --- Condition parsing round-trip on demo conditions ---

    def test_send_email_condition_parseable(self, jobs_by_name):
        cond_str = jobs_by_name["send_success_email"].condition
        node = parse_condition(cond_str)
        assert isinstance(node, AndNode)
        deps = list_job_dependencies(node)
        assert "generate_report"   in deps
        assert "load_to_warehouse" in deps

    # --- Variable substitution on demo commands ---

    def test_check_command_date_substitution(self, jobs_by_name):
        cmd = jobs_by_name["check_source_ready"].command
        expanded = substitute(cmd, run_date=date(2026, 6, 25))
        assert "%%DATE%%" not in expanded
        assert "06252026" in expanded

    def test_extract_command_date_substitution(self, jobs_by_name):
        cmd = jobs_by_name["extract_sales"].command
        expanded = substitute(cmd, run_date=date(2026, 6, 25))
        assert "06252026" in expanded
