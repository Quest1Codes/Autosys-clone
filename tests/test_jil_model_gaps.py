"""Model / DB / writer layer gaps M1-M9 (see jil_lexer_gap_findings)."""
from __future__ import annotations

import json
import sqlite3

import pytest
from pydantic import ValidationError

from autosys.db.connection import reset_engines, sync_session
from autosys.db.migrations import create_all_sync
from autosys.db.repository import jobs as job_repo, machines as machine_repo
from autosys.models.enums import JobType
from autosys.models.job import (
    BoxJob, CmdJob, FilewatchJob, FtpJob, Job, attribute_job_type_warnings,
    normalize_job_type, parse_job,
)
from autosys.models.machine import MachineDef
from autosys.parser.jil_writer import job_to_jil, machine_to_jil


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'gaps.db'}")
    reset_engines()
    create_all_sync()
    yield tmp_path / "gaps.db"
    reset_engines()


def _cmd(**kw):
    d = {"job_name": "j1", "command": "ls", "machine": "m1"}
    d.update(kw)
    return d


# ---------------- M1 ----------------
class TestJobType:
    @pytest.mark.parametrize("raw,cls", [
        ("c", CmdJob), ("CMD", CmdJob), ("cmd", CmdJob), ("C", CmdJob),
        ("b", BoxJob), ("box", BoxJob), ("BOX", BoxJob),
    ])
    def test_aliases(self, raw, cls):
        d = {"job_name": "j", "job_type": raw}
        if cls is CmdJob:
            d.update(command="x", machine="m")
        assert type(parse_job(d)) is cls

    @pytest.mark.parametrize("raw", ["f", "fw", "FW", "filewatch", "F"])
    def test_filewatch_aliases(self, raw):
        j = parse_job({"job_name": "j", "job_type": raw, "watch_file": "/x"})
        assert type(j) is FilewatchJob and j.job_type == "FILEWATCH"

    def test_ftp_and_ft_are_distinct(self):
        assert normalize_job_type("ftp") == "FTP"
        assert normalize_job_type("ft") == "FT"
        assert type(parse_job({"job_name": "t", "job_type": "FT"})) is Job

    @pytest.mark.parametrize("raw", [None, "", "  "])
    def test_default_cmd(self, raw):
        j = parse_job(_cmd(job_type=raw))
        assert type(j) is CmdJob
        assert type(parse_job(_cmd())) is CmdJob

    def test_default_cmd_still_validates(self):
        with pytest.raises(ValidationError):
            parse_job({"job_name": "j"})

    def test_all_pdf_types_accepted_generic_no_requirements(self):
        codes = ("OMTF HTTP SQL SCP DBTRIG ZOS ZOSM ZOSDST I5 WSDOC SNMPGET SNMPSET "
                 "JMSPUB JMSSUB HDFS SQOOP PIG OOZIE HIVE SAPBDC SAPEVT SAPJC SAPPM "
                 "SAPDA SAPBWIP SAPBWPC DBMON DBPROC ENTYBEAN SESSBEAN JAVARMI POJO "
                 "JMXMAG JMXMAS JMXMC JMXMOP JMXMREM JMXSUB OACOPY OASET OASG OMCPU "
                 "OMD OMEL OMIP OMP OMS PAPROC PAREQ PROXY WBSVC SQLAGENT FT").split()
        assert len(codes) >= 50
        for c in codes:
            j = parse_job({"job_name": "j", "job_type": c.lower()})
            assert j.job_type == c and type(j) is Job

    def test_ps_alias(self):
        assert parse_job({"job_name": "j", "job_type": "ps"}).job_type == "PEOPLESOFT"

    def test_unknown_type_becomes_user_defined_and_keeps_its_name(self):
        # user-defined job types (insert_job_type) cannot be known at parse
        # time; the original name is kept and written back.
        j = parse_job({"job_name": "j", "job_type": "MY_TYPE", "command": "5"})
        assert j.job_type == "USERDEFINED"
        assert j.extra_attrs["user_job_type"] == "MY_TYPE"
        assert "job_type: MY_TYPE" in job_to_jil(j)
        assert "user_job_type" not in job_to_jil(j)

    def test_writer_emits_fw(self):
        j = parse_job({"job_name": "w", "job_type": "f", "watch_file": "/x"})
        assert "job_type: FW" in job_to_jil(j)
        assert type(parse_job({"job_name": "w", "job_type": "FW", "watch_file": "/x"})) is FilewatchJob


# ---------------- M2 ----------------
class TestExtras:
    def test_unknown_keys_go_to_extras(self):
        j = parse_job(_cmd(job_type="CMD", sap_client="100", zzz=5, blob_input="a b"))
        assert j.extra_attrs == {"sap_client": "100", "zzz": "5", "blob_input": "a b"}
        assert "job_type" not in j.extra_attrs and "command" not in j.extra_attrs

    def test_default_empty(self):
        assert parse_job(_cmd()).extra_attrs == {}

    def test_writer_emits_extras_in_order(self):
        j = parse_job(_cmd(zeta="1", alpha="two words"))
        lines = job_to_jil(j).splitlines()
        assert lines[-2:] == ["zeta: 1", 'alpha: "two words"']
        assert lines.index("command: ls") < lines.index("zeta: 1")

    def test_db_round_trip_and_writer(self, db):
        j = parse_job(_cmd(oozie_x="q", j2ee_y="v"))
        with sync_session() as s:
            job_repo.upsert(s, j)
        with sync_session() as s:
            back = job_repo.get(s, "j1")
        assert back.extra_attrs == {"oozie_x": "q", "j2ee_y": "v"}
        text = job_to_jil(back)
        assert "oozie_x: q" in text and "j2ee_y: v" in text

    def test_update_replaces_extras(self, db):
        with sync_session() as s:
            job_repo.upsert(s, parse_job(_cmd(a="1")))
        with sync_session() as s:
            assert job_repo.upsert(s, parse_job(_cmd(b="2"))) == "updated"
        with sync_session() as s:
            assert job_repo.get(s, "j1").extra_attrs == {"b": "2"}


# ---------------- M2b ----------------
class TestMigration:
    def test_adds_missing_columns_keeps_rows(self, tmp_path, monkeypatch):
        path = tmp_path / "old.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE ujo_job (job_name VARCHAR(255) PRIMARY KEY, "
                    "job_type VARCHAR(16) NOT NULL, command TEXT)")
        con.execute("INSERT INTO ujo_job VALUES ('old','CMD','ls')")
        con.execute("CREATE TABLE ujo_machine (machine_name VARCHAR(255) PRIMARY KEY, "
                    "host VARCHAR(255) NOT NULL, port INTEGER NOT NULL DEFAULT 7520, "
                    "status VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN')")
        con.commit(); con.close()
        monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{path}")
        reset_engines()
        try:
            create_all_sync()
            create_all_sync()   # idempotent
            con = sqlite3.connect(path)
            cols = {r[1] for r in con.execute("PRAGMA table_info(ujo_job)")}
            mcols = {r[1] for r in con.execute("PRAGMA table_info(ujo_machine)")}
            assert {"extra_attrs_json", "n_retrys", "box_terminator", "created_at"} <= cols
            assert "members_json" in mcols
            assert con.execute("SELECT job_name, command FROM ujo_job").fetchall() == [("old", "ls")]
            con.close()
            # and the ORM can now read/write the old-schema table
            with sync_session() as s:
                job_repo.upsert(s, parse_job(_cmd(x="1")))
            with sync_session() as s:
                assert job_repo.get(s, "j1").extra_attrs == {"x": "1"}
        finally:
            reset_engines()


# ---------------- M4 ----------------
class TestFtp:
    LEGACY = dict(job_name="f", job_type="FTP", ftp_server="h", ftp_user="u",
                  ftp_type="GET", ftp_src="/r", ftp_dest="/l")

    def test_legacy_still_works(self):
        j = parse_job(dict(self.LEGACY))
        assert type(j) is FtpJob and j.ftp_type == "GET"

    def test_pdf_download_default(self):
        j = parse_job({"job_name": "f", "job_type": "FTP", "machine": "a",
                       "ftp_server_name": "hp", "ftp_server_port": "5222",
                       "ftp_transfer_type": "B", "ftp_remote_name": "/r",
                       "ftp_local_name": "/l", "owner": "test@hp"})
        assert (j.ftp_server, j.ftp_type, j.ftp_src, j.ftp_dest, j.ftp_user) == \
            ("hp", "GET", "/r", "/l", "test")
        assert j.extra_attrs["ftp_server_port"] == "5222"

    def test_pdf_upload_and_anonymous(self):
        j = parse_job({"job_name": "f", "job_type": "ftp", "ftp_server_name": "hp",
                       "ftp_transfer_direction": "UPLOAD", "ftp_remote_name": "/tmp",
                       "ftp_local_name": "/x/text*", "ftp_use_SSL": "TRUE"})
        assert (j.ftp_type, j.ftp_src, j.ftp_dest, j.ftp_user) == \
            ("PUT", "/x/text*", "/tmp", "anonymous")
        assert j.extra_attrs["ftp_use_SSL"] == "TRUE"

    def test_lowercase_variants_and_missing(self):
        j = parse_job({"job_name": "f", "job_type": "FTP", "ftp_server_name": "h",
                       "ftp_remote_name": "/r", "ftp_local_name": "/l",
                       "ftp_use_ssl": "false"})
        assert j.ftp_type == "GET"
        with pytest.raises(ValidationError):
            parse_job({"job_name": "f", "job_type": "FTP", "ftp_server_name": "h"})

    def test_writer_no_duplicate_vocab_and_db(self, db):
        j = parse_job({"job_name": "f", "job_type": "FTP", "ftp_server_name": "h",
                       "ftp_remote_name": "/r", "ftp_local_name": "/l"})
        with sync_session() as s:
            job_repo.upsert(s, j)
        with sync_session() as s:
            back = job_repo.get(s, "f")
        text = job_to_jil(back)
        assert "ftp_server_name: h" in text and "ftp_server:" not in text
        assert back.ftp_src == "/r"


# ---------------- M5 ----------------
class TestMachineMembers:
    def test_model_default(self):
        assert MachineDef(machine_name="x").members == []

    def test_round_trip(self, db):
        members = [{"machine": "cheetah", "max_load": 400, "factor": 5.0},
                   {"machine": "lily", "max_load": None, "factor": 2.0}]
        md = MachineDef(machine_name="giraffe", type="v", members=members)
        with sync_session() as s:
            machine_repo.register(s, "giraffe", "giraffe",
                                  members_json=json.dumps(md.members))
        with sync_session() as s:
            row = machine_repo.get(s, "giraffe")
            assert json.loads(row.members_json) == members
            out = machine_to_jil(row)
        assert "type: v" in out and out.count("\n    machine:") == 2 and "factor: 5.0" in out
        assert "max_load: 400" in out

    def test_register_update_keeps_members_when_not_given(self, db):
        with sync_session() as s:
            machine_repo.register(s, "vm", "vm", members_json='[{"machine": "a"}]')
        with sync_session() as s:
            machine_repo.register(s, "vm", "vm2")
        with sync_session() as s:
            assert machine_repo.get(s, "vm").members_json == '[{"machine": "a"}]'


# ---------------- M6 ----------------
class TestRelaxed:
    def test_zero_values(self):
        j = parse_job(_cmd(term_run_time=0, max_run_alarm=0, min_run_alarm=0))
        assert (j.term_run_time, j.max_run_alarm, j.min_run_alarm) == (0, 0, 0)

    def test_negative_still_rejected(self):
        with pytest.raises(ValidationError):
            parse_job(_cmd(term_run_time=-1))

    @pytest.mark.parametrize("v,exp", [(-1, False), ("-1", False), (0, False),
                                       (1, True), ("1", True), ("y", True), (False, False)])
    def test_auto_delete(self, v, exp):
        assert parse_job(_cmd(auto_delete=v)).auto_delete is exp

    def test_zero_is_unset_downstream(self, db):
        """term_run_time=0 must not kill a running job; alarms must not fire."""
        from datetime import timedelta
        from autosys.timeutil import utcnow
        from autosys.db.schema import JobRow
        from autosys.models.enums import JobStatus
        with sync_session() as s:
            job_repo.upsert(s, parse_job(_cmd(term_run_time=0, max_run_alarm=0, min_run_alarm=0)))
        with sync_session() as s:
            row = s.get(JobRow, "j1")
            row.status = JobStatus.RUNNING.value
            row.last_start = utcnow() - timedelta(hours=5)
            s.flush()
            from autosys.scheduler.event_processor import EventProcessor
            EventProcessor()._check_term_run_time(s, utcnow())
            s.flush()
            assert row.status == JobStatus.RUNNING.value
            # positive control: a real limit does terminate
            row.term_run_time = 1
            s.flush()
            EventProcessor()._check_term_run_time(s, utcnow())
            s.flush()
            assert row.status != JobStatus.RUNNING.value

    def test_zero_alarms_are_falsy(self):
        # alarm_manager / agent dispatch gate on truthiness (`if job.max_run_alarm`),
        # so 0 means "no alarm / no kill timer".
        j = parse_job(_cmd(max_run_alarm=0, min_run_alarm=0))
        assert not j.max_run_alarm and not j.min_run_alarm


# ---------------- M7 ----------------
class TestNames:
    @pytest.mark.parametrize("n", ["job#1", "a@b", "x#y@z.q-1:2"])
    def test_hash_at(self, n):
        assert parse_job(_cmd(job_name=n)).job_name == n

    def test_names_outside_the_pdf_charset_are_accepted_with_a_warning(self):
        from autosys.models.job import job_name_warnings
        assert parse_job(_cmd(job_name="a b")).job_name == "a b"
        assert job_name_warnings("a b") and not job_name_warnings("ok_name#1")

    def test_db_round_trip(self, db):
        with sync_session() as s:
            job_repo.upsert(s, parse_job(_cmd(job_name="j#1@x")))
        with sync_session() as s:
            assert job_repo.get(s, "j#1@x") is not None


# ---------------- M9 ----------------
class TestWarnings:
    def test_ftp_on_cmd(self):
        w = attribute_job_type_warnings(parse_job(_cmd(ftp_remote_name="/x")))
        assert len(w) == 1 and "ftp_remote_name" in w[0] and "CMD" in w[0]

    def test_sap_on_cmd_and_legacy_ftp_field(self):
        j = parse_job(_cmd(sap_client="1", ftp_server="h"))
        w = attribute_job_type_warnings(j)
        assert any("sap_client" in x for x in w) and any("ftp_server" in x for x in w)

    def test_clean_when_matching(self):
        j = parse_job({"job_name": "s", "job_type": "SAPJC", "sap_client": "1"})
        assert attribute_job_type_warnings(j) == []
        assert attribute_job_type_warnings(parse_job(_cmd())) == []
        f = parse_job({"job_name": "f", "job_type": "FTP", "ftp_server_name": "h",
                       "ftp_remote_name": "/r", "ftp_local_name": "/l"})
        assert attribute_job_type_warnings(f) == []

    def test_oozie_j2ee_zos(self):
        w = attribute_job_type_warnings(parse_job(_cmd(oozie_a="1", j2ee_b="2", zos_c="3")))
        assert len(w) == 3
