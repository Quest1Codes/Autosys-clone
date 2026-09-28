"""
Regression tests for the JIL lexer / parser gap analysis (L1-L16, plus the
parser-side items).  Each class is named after the gap it closes; the vendor
PDF rule it implements is quoted where relevant.
"""

from __future__ import annotations

import time

import pytest

from autosys.parser.lexer import Lexer, LexError, TokenKind, strip_comments, tokenize
from autosys.parser.jil_parser import JILParseError, parse_jil


def toks(text: str):
    """(kind, value) pairs without EOF."""
    return [(t.kind.value, t.value) for t in tokenize(text) if t.kind != TokenKind.EOF]


def attrs(text: str) -> list[tuple[str, str]]:
    out, it = [], iter(toks(text))
    for kind, val in it:
        if kind == "ATTR_NAME":
            out.append((val, next(it)[1]))
    return out


HDR = "insert_job: j   job_type: CMD\n"


# ---------------------------------------------------------------------------
class TestL1HashComments:
    """PDF Rule 7: '#' in the first column comments out the line."""

    def test_full_line_hash_comment_before_stanza(self):
        assert toks("# nightly load\n" + HDR)[0] == ("DIRECTIVE", "insert_job")

    def test_hash_comment_between_attributes(self):
        assert attrs(HDR + "command: x\n#owner: nobody\nmachine: m\n") == [
            ("job_type", "CMD"), ("command", "x"), ("machine", "m")]

    def test_indented_hash_comment(self):
        assert attrs(HDR + "   # indented\ncommand: x") == [("job_type", "CMD"), ("command", "x")]

    def test_comment_only_file(self):
        assert toks("# a\n# b\n") == []

    def test_hash_inside_a_value_is_kept(self):
        assert ("command", "echo a # b") in attrs(HDR + "command: echo a # b")
        assert ("command", "#!/bin/sh") in attrs(HDR + "command: #!/bin/sh")


# ---------------------------------------------------------------------------
class TestL2BlockComments:
    """PDF: comments are not scanned inside quotes; /* needs a preceding blank."""

    def test_comment_inside_quoted_value_is_kept(self):
        got = attrs(HDR + 'description: "This /* includes */ chars"')
        assert ("description", "This /* includes */ chars") in got

    def test_glob_paths_survive(self):
        assert ("command", "cp /a/*/x /b/*/y") in attrs(HDR + "command: cp /a/*/x /b/*/y")

    def test_pdf_example_rm_glob_with_trailing_comment(self):
        got = attrs(HDR + "command: rm $HOME/test/* /* delete files */")
        assert ("command", "rm $HOME/test/*") in got

    def test_pdf_example_quoted_slash_star(self):
        assert ("command", 'ls "/*"') in attrs(HDR + 'command: ls "/*" /* argument */')

    def test_glob_does_not_swallow_a_later_stanza(self):
        text = HDR + "command: ls /tmp/*\n\ninsert_job: b   job_type: BOX\n/* c */\n"
        names = [v for k, v in toks(text) if k == "JOB_NAME"]
        assert names == ["j", "b"]

    def test_normal_block_comments_still_work(self):
        assert attrs(HDR + "/* multi\nline */\ncommand: x /* trail */") == [
            ("job_type", "CMD"), ("command", "x")]

    def test_close_must_be_followed_by_blank(self):
        assert strip_comments("a /* c */b").startswith("a /* c */b")

    def test_unterminated_comment_is_linear_time(self):
        text = "a /* " * 20000
        t0 = time.time()
        strip_comments(text)
        assert time.time() - t0 < 2.0


# ---------------------------------------------------------------------------
class TestL3L4MultipleStatementsPerLine:
    """PDF Rule 3: several attribute statements on one line."""

    def test_body_line_split(self):
        assert attrs(HDR + "machine: m  owner: bob  command: echo") == [
            ("job_type", "CMD"), ("machine", "m"), ("owner", "bob"), ("command", "echo")]

    def test_header_multiword_value(self):
        text = "insert_job: a job_type: CMD command: echo hi machine: m"
        assert attrs(text) == [("job_type", "CMD"), ("command", "echo hi"), ("machine", "m")]

    def test_header_quoted_value_with_spaces(self):
        assert ("description", "hello world") in attrs(
            'insert_job: a job_type: CMD description: "hello world"')

    def test_free_text_with_unknown_colon_word_is_not_split(self):
        assert ("description", "foo bar: baz") in attrs(HDR + "description: foo bar: baz")

    def test_common_word_mid_line_is_not_split(self):
        # 'type' is an attribute AND an English word
        assert ("description", "Server type: web") in attrs(HDR + "description: Server type: web")

    def test_empty_inline_value(self):
        assert ("box_name", "") in attrs("insert_job: a job_type: CMD box_name:")

    def test_leading_boundary_on_header(self):
        # 'Owner:' is a valid (case-insensitive) key, not the attribute 'wner'
        assert ("owner", "x") in attrs("insert_job: a job_type: BOX Owner: x")

    def test_trailing_junk_after_single_token_value_is_an_error(self):
        with pytest.raises(LexError):
            tokenize("insert_job: a job_type: CMD garbage")

    def test_junk_after_header_is_an_error(self):
        with pytest.raises(LexError):
            tokenize("insert_job: a garbage words")


# ---------------------------------------------------------------------------
class TestL5Quotes:

    def test_single_quoted_token_loses_quotes(self):
        assert ("command", "echo hi") in attrs(HDR + 'command: "echo hi"')

    def test_quoted_list_kept_whole(self):
        assert ("start_times", '"10:00","11:00"') in attrs(HDR + 'start_times: "10:00","11:00"')

    def test_two_quoted_words_kept_verbatim(self):
        got = attrs(HDR + 'command: "c:\\Programs\\pay.exe" "C:\\Pay Data\\s.dat"')
        assert ("command", '"c:\\Programs\\pay.exe" "C:\\Pay Data\\s.dat"') in got

    def test_shell_and_and_kept(self):
        assert ("command", '"a" && "b"') in attrs(HDR + 'command: "a" && "b"')

    def test_empty_quoted_value(self):
        assert ("description", "") in attrs(HDR + 'description: ""')

    def test_unterminated_quoted_value_is_an_error(self):
        with pytest.raises(LexError):
            tokenize(HDR + 'command: "echo hi')

    def test_parsed_start_times_still_a_list(self):
        job = parse_jil('insert_job: b job_type: BOX\nstart_times: "10:00","11:00"\n')[0].job
        assert job.start_times == ["10:00", "11:00"]


# ---------------------------------------------------------------------------
class TestL6Escapes:

    def test_escaped_colon_in_time(self):
        assert ("start_times", "10:00, 14:00") in attrs(HDR + "start_times: 10\\:00, 14\\:00")

    def test_escaped_colon_windows_path(self):
        assert ("std_out_file", "C:\\logs\\o.txt") in attrs(HDR + "std_out_file: C\\:\\logs\\o.txt")

    def test_escaped_quote_inside_quotes(self):
        assert ("description", 'say "hi"') in attrs(HDR + 'description: "say \\"hi\\""')

    def test_escaped_comma_and_quote_in_j2ee_value(self):
        assert ("j2ee_parameter", 'String="Hello1, World"') in attrs(
            HDR + 'j2ee_parameter: String=\\"Hello1\\, World\\"')

    def test_escaped_quote_in_shell_command_is_preserved(self):
        assert ("command", 'echo \\"hi\\"') in attrs(HDR + 'command: echo \\"hi\\"')

    def test_escaped_colon_does_not_split_statements(self):
        assert attrs(HDR + "description: see machine\\: x") == [
            ("job_type", "CMD"), ("description", "see machine: x")]


# ---------------------------------------------------------------------------
class TestL7Blobs:
    """PDF Rule 8: <auto_blobt> ... </auto_blobt> is literal and multi-line."""

    def test_multiline_blob(self):
        text = HDR + "blob_input:<auto_blobt> line1\nowner: fake\n/* keep */\nline3\n</auto_blobt>\nmachine: m\n"
        got = dict(attrs(text))
        assert "owner: fake" in got["blob_input"] and "/* keep */" in got["blob_input"]
        assert got["machine"] == "m"
        assert "owner" not in got          # not lexed as an attribute

    def test_blob_on_insert_glob(self):
        text = "insert_glob: g\nblob_mode: text\nblob_input: <auto_blobt>multi\nline</auto_blobt>\n"
        assert dict(attrs(text))["blob_input"] == "multi\nline"

    def test_unterminated_blob(self):
        with pytest.raises(LexError):
            tokenize(HDR + "blob_input: <auto_blobt> never closed\nmore\n")

    def test_blob_containing_directive_text(self):
        text = HDR + "blob_input: <auto_blobt>\ninsert_job: evil job_type: BOX\n</auto_blobt>\n"
        assert [v for k, v in toks(text) if k == "DIRECTIVE"] == ["insert_job"]


# ---------------------------------------------------------------------------
class TestL8Continuation:

    def test_condition_continued_on_next_line(self):
        got = dict(attrs(HDR + "condition: done(A) and\ndone(B) and done(C)\nmachine: m"))
        assert got["condition"] == "done(A) and done(B) and done(C)"
        assert got["machine"] == "m"

    def test_resources_continued(self):
        got = dict(attrs(HDR + "resources: (D1, QUANTITY=1) AND\n(R1, QUANTITY=4) AND\n(T1, QUANTITY=3)"))
        assert got["resources"].count("AND") == 2

    def test_keyword_and_value_on_separate_lines(self):
        assert ("owner", "user1") in attrs(HDR + "owner\n:user1")

    def test_empty_value_then_value_line(self):
        assert ("hadoop_hdfs_preserve_status", "true") in attrs(HDR + "hadoop_hdfs_preserve_status:\ntrue")

    def test_multiline_quoted_value(self):
        got = dict(attrs(HDR + 'command: "echo\n hi"\nmachine: m'))
        assert got["command"] == "echo\n hi" and got["machine"] == "m"

    def test_stray_line_after_header_is_still_an_error(self):
        with pytest.raises(LexError):
            tokenize("insert_job: a job_type: CMD\nnot an attribute\n")

    def test_stray_first_line_is_an_error(self):
        with pytest.raises(LexError):
            tokenize("garbage line\n")


# ---------------------------------------------------------------------------
class TestL9MixedCaseKeys:

    @pytest.mark.parametrize("key", ["URL", "WSDL_URL", "endpoint_URL", "ftp_use_SSL"])
    def test_pdf_mixed_case_attribute_accepted(self, key):
        assert (key, "v") in attrs(HDR + f"{key}: v")

    def test_case_is_preserved_only_for_documented_names(self):
        assert ("command", "x") in attrs(HDR + "Command: x")
        assert ("ftp_use_SSL", "TRUE") in attrs(HDR + "FTP_USE_SSL: TRUE")

    def test_mixed_case_on_header_line(self):
        assert ("ftp_use_SSL", "TRUE") in attrs("insert_job: a job_type: FT ftp_use_SSL: TRUE")


# ---------------------------------------------------------------------------
class TestL10Directives:

    @pytest.mark.parametrize("line", [
        "delete_box: b1",
        "insert_view: v1",
        "modify_view: v1",
        "delete_view: v1",
        "insert_filter: f1",
        "delete_filter: f1",
        "insert_alert_policy: FailedJobs",
        "modify_alert_policy: FailedJobs",
        "delete_alert_policy: FailedJobs",
        "update_blob: b",
        "update_glob: g",
        "update_connectionprofile: c",
        "delete_user: bob",
    ])
    def test_new_directive_lexes_and_parses(self, line):
        d = line.split(":")[0]
        assert toks(line)[0] == ("DIRECTIVE", d)
        assert parse_jil(line + "\n")[0].op == d

    def test_delete_box_is_a_job_op(self):
        op = parse_jil("delete_box: EOD_box\n")[0]
        assert op.op == "delete_box" and op.job.job_name == "EOD_box"

    def test_unknown_subcommand_is_rejected_not_swallowed(self):
        with pytest.raises(LexError):
            tokenize(HDR + "insert_bogus: x\n")

    def test_attribute_names_that_look_like_directives_are_still_attributes(self):
        # modify_parameter is a real job attribute, not a sub-command
        assert ("modify_parameter", 'String="1"') in attrs(HDR + 'modify_parameter: String="1"')

    def test_alert_policy_stanza_body(self):
        op = parse_jil("insert_alert_policy: FailedJobs\njob_status: TERMINATED\nseverity: HIGH\n")[0]
        assert op.raw_attrs["policy_name"] == "FailedJobs"
        assert op.raw_attrs["severity"] == "HIGH"


# ---------------------------------------------------------------------------
class TestL11OverrideDelete:

    def test_override_delete_flag(self):
        op = parse_jil("override_job: RunData delete\n")[0]
        assert op.op == "override_delete" and op.job.job_name == "RunData"

    def test_override_without_delete_is_plain_override(self):
        op = parse_jil("override_job: RunData\ncommand: x\n")[0]
        assert op.op == "override"


# ---------------------------------------------------------------------------
class TestL12L13L14Robustness:

    def test_utf8_bom(self):
        assert toks("\ufeff" + HDR)[0] == ("DIRECTIVE", "insert_job")

    def test_cr_only_line_endings(self):
        got = attrs("insert_job: a job_type: CMD\rcommand: echo hello world\rmachine: m\r")
        assert ("command", "echo hello world") in got and ("machine", "m") in got

    def test_crlf(self):
        assert ("command", "x") in attrs("insert_job: a job_type: CMD\r\ncommand: x\r\n")

    def test_exit_terminator_ignored(self):
        assert toks(HDR + "command: x\nEXIT\n") == toks(HDR + "command: x\n")
        assert toks(HDR + "command: x\nexit") == toks(HDR + "command: x\n")

    def test_parse_jil_file_reads_bom(self, tmp_path):
        from autosys.parser.jil_parser import parse_jil_file
        f = tmp_path / "b.jil"
        f.write_bytes(b"\xef\xbb\xbfinsert_job: a job_type: BOX\n")
        assert parse_jil_file(str(f))[0].job.job_name == "a"

    def test_long_whitespace_run_is_fast(self):
        t0 = time.time()
        tokenize(HDR + "command: x" + " " * 200000 + "y\n")
        assert time.time() - t0 < 2.0

    def test_many_stanzas_fast(self):
        text = "".join(f"insert_job: j{i} job_type: BOX\nowner: o\n" for i in range(20000))
        t0 = time.time()
        tokenize(text)
        assert time.time() - t0 < 5.0


# ---------------------------------------------------------------------------
class TestL15ObjectNames:

    def test_quoted_name(self):
        assert toks('insert_job: "a b" job_type: BOX')[1] == ("JOB_NAME", "a b")

    def test_quoted_name_with_colon(self):
        assert toks('insert_job: "a:b" job_type: BOX')[1] == ("JOB_NAME", "a:b")

    @pytest.mark.parametrize("q", ["'V1 Default'", "\u2018V1 Default\u2019", "\u201cV1 Default\u201d"])
    def test_single_and_typographic_quoted_names(self, q):
        # PDF alert-policy example: insert_alert_policy: 'View1-Default Alert Filter=ABC'
        assert toks(f"insert_alert_policy: {q}\nseverity: HIGH")[1] == ("JOB_NAME", "V1 Default")

    def test_escaped_colon_name(self):
        assert toks("insert_job: a\\:b job_type: BOX")[1] == ("JOB_NAME", "a:b")

    def test_hash_name_parses(self):
        assert parse_jil("insert_job: a#b job_type: BOX\n")[0].job.job_name == "a#b"

    def test_missing_name_is_a_clear_error(self):
        with pytest.raises(LexError):
            tokenize("insert_job:\n")


# ---------------------------------------------------------------------------
class TestL16CaseAndCharset:

    def test_directive_case_insensitive(self):
        assert toks("INSERT_JOB: a job_type: BOX")[0] == ("DIRECTIVE", "insert_job")
        assert toks("Insert_Job: a")[0] == ("DIRECTIVE", "insert_job")

    def test_key_charset_allows_hyphen_dot_digits(self):
        assert ("foo-bar", "1") in attrs(HDR + "foo-bar: 1")
        assert ("foo.bar", "1") in attrs(HDR + "foo.bar: 1")
        assert ("j2ee_x", "1") in attrs(HDR + "J2EE_X: 1")


# ---------------------------------------------------------------------------
class TestParserOps:

    def test_attr_pairs_keep_repeated_keys(self):
        op = parse_jil("insert_machine: vm\ntype: v\nmachine: a\nmachine: b\n")[0]
        assert [v for k, v in op.attr_pairs if k == "machine"] == ["a", "b"]

    def test_null_on_update_is_recorded(self):
        op = parse_jil("update_job: a\ndescription: NULL\n")[0]
        assert op.null_attrs == ["description"]
        assert "description" not in op.coerced

    def test_null_on_insert_is_dropped(self):
        op = parse_jil("insert_job: a job_type: CMD\ncommand: x\nmachine: m\ndescription: NULL\n")[0]
        assert op.null_attrs == [] and op.job.description is None

    def test_update_with_job_type_does_not_require_full_definition(self):
        op = parse_jil("update_job: a job_type: CMD\ncommand: y\n")[0]
        assert op.op == "update" and op.job.command == "y"

    def test_delete_job_accepts_stray_attributes(self):
        assert parse_jil("delete_job: a\njob_type: CMD\n")[0].op == "delete"
