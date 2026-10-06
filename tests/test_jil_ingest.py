"""Lossless, error-proof JIL ingestion (autosys.parser.jil_ingest)."""
from __future__ import annotations

import codecs

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from autosys.db.schema import Base, JilFileRow, JilStanzaRow
from autosys.parser.jil_ingest import decode_bytes, ingest_paths, iter_files
from autosys.parser.jil_parser import JILParser


@pytest.fixture
def sf(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path/'i.db'}")
    Base.metadata.create_all(eng)
    return sessionmaker(eng, autoflush=False)


def _run(sf, tmp_path, files: dict, **kw):
    for name, data in files.items():
        (tmp_path / name).write_bytes(data if isinstance(data, bytes) else data.encode())
    paths = [p for p in iter_files(tmp_path, "*.jil")]
    return ingest_paths(sf, paths, **kw)


def _stanzas(sf):
    with sf() as s:
        return list(s.execute(select(JilStanzaRow).order_by(JilStanzaRow.file_id, JilStanzaRow.seq)).scalars())


GOOD = "insert_job: a job_type: CMD\ncommand: x\nmachine: m\n"


class TestDecode:
    @pytest.mark.parametrize("data,enc", [
        (codecs.BOM_UTF8 + b"hi", "utf-8-sig"),
        ("hi".encode("utf-16"), "utf-16"),
        ("hi there".encode("utf-16-le"), "utf-16-le"),
        (b"caf\xe9", "cp1252"),
        (b"\x81\x8d\x8f", "latin-1"),
        (b"plain", "utf-8"),
        (b"", "utf-8"),
    ])
    def test_never_fails(self, data, enc):
        text, got, _ = decode_bytes(data)
        assert got == enc and isinstance(text, str)

    def test_non_utf8_warns(self):
        assert decode_bytes(b"caf\xe9")[2][0]["code"] == "encoding"


class TestConservation:
    def test_raw_text_reassembles(self, sf, tmp_path):
        text = GOOD + "\n# note\nupdate_job: zzz\ncommand: y\n\nprose here\n\ndelete_job: a\n"
        _run(sf, tmp_path, {"f.jil": text})
        rows = _stanzas(sf)
        assert "".join(r.raw_text for r in rows) == text

    def test_every_stanza_has_disposition(self, sf, tmp_path):
        s = _run(sf, tmp_path, {"f.jil": GOOD + "\n@@@ garbage\ninsert_job: b job_type: NOPE\n"})
        assert s.stanzas == sum(s.dispositions.values())

    def test_empty_file_zero_rows(self, sf, tmp_path):
        _run(sf, tmp_path, {"e.jil": ""})
        assert _stanzas(sf) == []
        with sf() as s:
            assert s.execute(select(JilFileRow)).scalars().first().n_stanzas == 0

    def test_comment_only_archived(self, sf, tmp_path):
        s = _run(sf, tmp_path, {"c.jil": "# nothing\n"})
        assert s.dispositions == {"ARCHIVE_ONLY": 1}


class TestDuplicates:
    def test_first_wins_and_both_archived(self, sf, tmp_path):
        s = _run(sf, tmp_path, {
            "a.jil": GOOD,
            "b.jil": GOOD.replace("command: x", "command: OTHER")})
        assert s.dispositions["DUPLICATE"] == 1
        assert len(_stanzas(sf)) == 2
        from autosys.db.schema import JobRow
        with sf() as se:
            assert se.execute(select(JobRow.command)).scalar() == "x"

    def test_last_wins(self, sf, tmp_path):
        _run(sf, tmp_path, {"a.jil": GOOD, "b.jil": GOOD.replace("command: x", "command: OTHER")},
             duplicates="last")
        from autosys.db.schema import JobRow
        with sf() as se:
            assert se.execute(select(JobRow.command)).scalar() == "OTHER"


class TestTolerance:
    def test_prose_and_unknown_types_do_not_raise(self, sf, tmp_path):
        s = _run(sf, tmp_path, {"f.jil": "hello world\n\ninsert_job: q job_type: WHATEVER\nfoo: 1\n"})
        assert s.stanzas >= 1 and s.file_status

    def test_update_before_insert_applied_later(self, sf, tmp_path):
        s = _run(sf, tmp_path, {"f.jil": "update_job: late\ncommand: new\n\n"
                                          "insert_job: late job_type: CMD\ncommand: old\nmachine: m\n"})
        assert s.deferred_applied == 1
        from autosys.db.schema import JobRow
        with sf() as se:
            assert se.execute(select(JobRow.command)).scalar() == "new"

    def test_reimport_replaces_archive(self, sf, tmp_path):
        _run(sf, tmp_path, {"f.jil": GOOD})
        _run(sf, tmp_path, {"f.jil": GOOD})
        assert len(_stanzas(sf)) == 1

    def test_nul_bytes(self, sf, tmp_path):
        s = _run(sf, tmp_path, {"n.jil": b"insert_job: a job_type: CMD\ncommand: x\x00y\nmachine: m\n"})
        assert s.files == 1

    def test_suspect_continuation_flagged(self):
        ops = JILParser().parse_text("insert_job: a job_type: CMD\nmachine: m\ncommand: d\n\nprose\n",
                                     tolerant=True)
        assert any(i["code"] == "suspect_continuation" for i in ops[0].issues)

    def test_stray_line_after_single_value_attribute_is_not_glued_on(self):   # PARSER-08
        ops = JILParser().parse_text("insert_job: a job_type: CMD\ncommand: d\nmachine: m1\n"
                                     "sendevent -E STARTJOB -J a\n", tolerant=True)
        assert ops[0].job.machine == "m1"
        assert any(i["code"] == "stray_line" for i in ops[0].issues)

    def test_wrapped_condition_is_flagged_every_time(self):                 # PARSER-08
        ops = JILParser().parse_text("insert_job: a job_type: CMD\ncommand: d\nmachine: m\n"
                                     "condition: s(x)\n & s(y)\n", tolerant=True)
        assert ops[0].job.condition == "s(x) & s(y)"
        assert any(i["code"] == "suspect_continuation" for i in ops[0].issues)


class TestDeadConnectionAborts:
    """A dead DB connection must stop the whole run, not fail every remaining
    file individually — see jil_ingest._connection_lost / ingest_paths."""

    def test_connection_invalidated_aborts_with_resume_point(self, sf, tmp_path, monkeypatch):
        from sqlalchemy.exc import OperationalError
        import autosys.parser.jil_ingest as jil_ingest

        files = {f"f{i}.jil": GOOD for i in range(5)}
        for name, data in files.items():
            (tmp_path / name).write_bytes(data.encode())
        paths = list(jil_ingest.iter_files(tmp_path, "*.jil"))

        real_ingest_file = jil_ingest.ingest_file
        calls = {"n": 0}

        def flaky(session, path, **kw):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OperationalError("SELECT 1", {}, Exception("server closed the connection "
                                       "unexpectedly"), connection_invalidated=True)
            return real_ingest_file(session, path, **kw)

        monkeypatch.setattr(jil_ingest, "ingest_file", flaky)
        summary = jil_ingest.ingest_paths(sf, paths, commit_every=1)

        assert summary.aborted is True
        assert "OperationalError" in summary.abort_reason
        assert summary.files_committed == 2          # the 2 that committed before file 3
        assert summary.last_path.endswith(".jil")
        # nothing after the break was attempted
        assert calls["n"] == 3

    def test_pending_rollback_error_also_aborts(self, sf, tmp_path, monkeypatch):
        from sqlalchemy.exc import PendingRollbackError
        import autosys.parser.jil_ingest as jil_ingest

        (tmp_path / "f0.jil").write_bytes(GOOD.encode())
        paths = list(jil_ingest.iter_files(tmp_path, "*.jil"))

        def broken(session, path, **kw):
            raise PendingRollbackError("Can't reconnect until invalid transaction is rolled back")

        monkeypatch.setattr(jil_ingest, "ingest_file", broken)
        summary = jil_ingest.ingest_paths(sf, paths, commit_every=1)
        assert summary.aborted is True
        assert summary.files_committed == 0

    def test_ordinary_content_error_does_not_abort(self, sf, tmp_path, monkeypatch):
        """A per-file bug (not a connection problem) still gets archived and the
        run continues — that behaviour must not regress."""
        import autosys.parser.jil_ingest as jil_ingest

        for i in range(3):
            (tmp_path / f"f{i}.jil").write_bytes(GOOD.encode())
        paths = list(jil_ingest.iter_files(tmp_path, "*.jil"))

        real_ingest_file = jil_ingest.ingest_file

        def flaky(session, path, **kw):
            if "f1" in str(path):
                raise ValueError("some unrelated bug")
            return real_ingest_file(session, path, **kw)

        monkeypatch.setattr(jil_ingest, "ingest_file", flaky)
        summary = jil_ingest.ingest_paths(sf, paths, commit_every=1)
        assert summary.aborted is False
        assert summary.files == 3
        assert summary.dispositions.get("QUARANTINED", 0) == 1


class TestNulEscape:
    def test_roundtrip(self):
        from autosys.parser.jil_ingest import escape_nul, unescape_nul
        for s in ["a\x00b", "a\\0b\x00\\\\", "\x00"]:
            e, flag = escape_nul(s)
            assert flag and "\x00" not in e and unescape_nul(e) == s
        assert escape_nul("plain\\0") == ("plain\\0", False)

    def test_nul_archived_escaped_and_jobs_loaded(self, sf, tmp_path):
        from autosys.parser.jil_ingest import unescape_nul
        data = b"insert_job: a job_type: CMD\ncommand: x\x00y\nmachine: m\n"
        _run(sf, tmp_path, {"n.jil": data})
        row = _stanzas(sf)[0]
        assert row.raw_escaped and unescape_nul(row.raw_text) == data.decode()
        from autosys.db.schema import JobRow
        with sf() as se:
            assert se.execute(select(JobRow.command)).scalar() == "xy"
