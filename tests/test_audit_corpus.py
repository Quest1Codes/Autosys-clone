"""
Regression suite built from the simulator audit's probe corpus.

tests/fixtures/jil/audit_corpus/ holds the JIL files the audit reviewers wrote
to probe real-world syntax: autorep output, lexical edge cases, every job
type, sub-commands, encodings, repeated attributes, short- vs long-form
conditions, stray lines and delete/re-insert sequences. Two large stress
inputs (a 1.3 MB stanza, 10,000 envvars) are generated here instead of being
committed.

Two kinds of test:
  * every file ingests without an exception and every stanza gets a
    disposition -- the import's "never fails on content" contract;
  * the specific findings fixed in this round stay fixed (PARSER-01,
    PARSER-02, SEM-13, SEM-01).
Findings not fixed yet are deliberately not asserted here; see
dev/task6-simulator-audit/simulator-audit.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from autosys.agent.runner import parse_envvars

CORPUS = Path(__file__).parent / "fixtures" / "jil" / "audit_corpus"
FILES = sorted(p for p in CORPUS.glob("*.jil"))
DISPOSITIONS = {"LOADED", "LOADED_WITH_WARNINGS", "ARCHIVE_ONLY", "QUARANTINED", "DUPLICATE"}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'corpus.db'}")
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    connection.reset_engines()
    create_all_sync()
    yield connection
    connection.reset_engines()


def _ingest(db, paths):
    from autosys.parser.jil_ingest import ingest_paths
    return ingest_paths(db.sync_session, [Path(p) for p in paths])


def _jobs(db):
    from autosys.db.schema import JobRow
    with db.sync_session() as s:
        return {r.job_name: r for r in s.scalars(select(JobRow))}


def test_corpus_is_present():
    assert len(FILES) >= 20


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_corpus_file_ingests_and_every_stanza_is_accounted_for(db, path):
    from autosys.db.schema import JilStanzaRow
    _ingest(db, [path])   # must not raise
    with db.sync_session() as s:
        dispositions = list(s.scalars(select(JilStanzaRow.disposition)))
    assert dispositions, f"{path.name}: nothing archived"
    assert set(dispositions) <= DISPOSITIONS


def test_delete_then_reinsert_sequence_ingests(db):
    _ingest(db, sorted((CORPUS / "24_delete_reinsert").glob("*.jil")))


# --- fixed findings --------------------------------------------------------

def test_repeated_attributes_keep_every_value(db):                 # PARSER-01
    _ingest(db, [CORPUS / "06_repeated.jil"])
    jobs = _jobs(db)
    assert parse_envvars(jobs["RP_ENV"].envvars) == {"A": "1", "B": "2", "C": "3"}
    import json
    extras = {name: json.loads(jobs[name].extra_attrs_json or "{}") for name in ("RP_SP", "RP_WS", "RP_SAP", "RP_POJO")}
    assert len(extras["RP_SP"]["sp_arg"].split("\n")) == 2
    assert len(extras["RP_WS"]["ws_parameter"].split("\n")) == 2
    assert len(extras["RP_SAP"]["sap_step_parms"].split("\n")) == 2
    assert len(extras["RP_POJO"]["j2ee_parameter"].split("\n")) == 2


def test_start_mins_stored_intact(db):                              # PARSER-02
    _ingest(db, [CORPUS / "01_autorep_core.jil"])
    assert _jobs(db)["PRD_CMD_02"].start_mins == "0,15,30,45"


def test_y_booleans_are_true(db):                                   # SEM-13
    _ingest(db, [CORPUS / "04_values.jil"])
    vb = _jobs(db)["VB_01"]
    assert vb.date_conditions and vb.alarm_if_fail and vb.alarm_if_terminated
    assert vb.box_terminator and vb.job_terminator


def test_short_and_long_conditions_give_the_same_blast_radius():   # SEM-01
    from autosys.analysis.dependency_graph import fan_in_counts
    from autosys.parser.jil_parser import JILParser
    from autosys.db.schema import JobRow
    ops = JILParser().parse_text((CORPUS / "19_dep_short_vs_long.jil").read_text(), tolerant=True)
    rows = {op.job.job_name: JobRow(job_name=op.job.job_name, job_type="CMD", condition=op.job.condition)
            for op in ops if op.job}
    fan = fan_in_counts(rows)
    assert fan["D_B_SHORT"] == fan["D_B_LONG"] == 1
    assert fan["D_A"] == 2


# --- generated stress inputs ----------------------------------------------

def test_ten_thousand_envvars_all_kept():
    from autosys.parser.jil_parser import JILParser
    jil = "insert_job: BIG_ENV  job_type: CMD\ncommand: x\nmachine: m\n" + \
          "".join(f"envvars: V{i}={i}\n" for i in range(10_000))
    op = JILParser().parse_text(jil, tolerant=True)[0]
    env = parse_envvars(op.job.envvars)
    assert len(env) == 10_000 and env["V0"] == "0" and env["V9999"] == "9999"


def test_megabyte_stanza_ingests(db, tmp_path):
    big = tmp_path / "big.jil"
    big.write_text("insert_job: BIG_DESC  job_type: CMD\ncommand: x\nmachine: m\n"
                   f'description: "{"d" * 1_300_000}"\n')
    _ingest(db, [big])
    assert len(_jobs(db)["BIG_DESC"].description) >= 1_300_000
