"""
JIL parser fixes from the simulator audit (PARSER-01, PARSER-02, SEM-13).

Each of these lost or corrupted data silently: the stanza was reported
LOADED with no issue.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from autosys.agent.runner import parse_envvars
from autosys.parser.jil_parser import JILParser

FIXTURES = Path(__file__).parent / "fixtures" / "jil"


def _job(jil: str):
    ops = JILParser().parse_text(jil, tolerant=True)
    assert len(ops) == 1, ops
    return ops[0]


# --- PARSER-01: repeated attributes ----------------------------------------

def test_repeated_envvars_are_all_kept():
    op = _job("insert_job: J  job_type: CMD\ncommand: x\nmachine: m\n"
              "envvars: A=1\nenvvars: B=2\nenvvars: C=3\n")
    assert parse_envvars(op.job.envvars) == {"A": "1", "B": "2", "C": "3"}
    assert not [i for i in op.issues if i["code"] == "repeated_attribute"]


def test_repeated_envvars_merge_with_comma_lists():
    op = _job("insert_job: J  job_type: CMD\ncommand: x\nmachine: m\n"
              "envvars: A=1, B=2\nenvvars: C=3\n")
    assert parse_envvars(op.job.envvars) == {"A": "1", "B": "2", "C": "3"}


@pytest.mark.parametrize("attr", ["sp_arg", "sap_step_parms", "ws_parameter", "j2ee_parameter"])
def test_repeated_type_specific_parameters_are_all_kept_and_round_trip(attr):
    from autosys.parser.jil_writer import job_to_jil
    jil = (f"insert_job: J  job_type: CMD\ncommand: x\nmachine: m\n"
           f"{attr}: first\n{attr}: second\n{attr}: third\n")
    op = _job(jil)
    value = op.job.extra_attrs[attr]
    assert value.split("\n") == ["first", "second", "third"]
    out = job_to_jil(op.job)
    assert [ln for ln in out.splitlines() if ln.startswith(f"{attr}:")] == \
           [f"{attr}: first", f"{attr}: second", f"{attr}: third"]


def test_repeated_single_value_attribute_is_flagged_not_silent():
    op = _job("insert_job: J  job_type: CMD\ncommand: x\nmachine: m1\nmachine: m2\n")
    assert op.job.machine == "m2"     # last wins, as before ...
    assert any(i["code"] == "repeated_attribute" and "machine" in i["message"]
               for i in op.issues)    # ... but no longer silently


# --- PARSER-02: start_mins -------------------------------------------------

def test_start_mins_survive_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'm.db'}")
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    from autosys.db.schema import JobRow
    from autosys.parser.jil_ingest import ingest_paths
    connection.reset_engines()
    create_all_sync()
    jil = tmp_path / "mins.jil"
    jil.write_text("insert_job: POLL  job_type: CMD\ncommand: x\nmachine: m\n"
                   "date_conditions: 1\ndays_of_week: all\nstart_mins: 0,15,30,45\n"
                   "\nupdate_job: POLL\ndescription: touched\n")
    ingest_paths(connection.sync_session, [jil])
    with connection.sync_session() as s:
        stored = s.scalars(select(JobRow.start_mins).where(JobRow.job_name == "POLL")).one()
    connection.reset_engines()
    assert stored == "0,15,30,45"


def test_start_mins_drive_the_right_minutes():
    from datetime import datetime
    from autosys.scheduler.time_trigger import _get_matching_start_mins
    op = _job("insert_job: P  job_type: CMD\ncommand: x\nmachine: m\nstart_mins: 0,15,30,45\n")
    fired = [m for m in range(60)
             if _get_matching_start_mins(op.job, datetime(2026, 10, 5, 10, m))]
    assert fired == [0, 15, 30, 45]


# --- SEM-13: y/n booleans --------------------------------------------------

@pytest.mark.parametrize("token, expected", [
    ("1", True), ("y", True), ("Y", True), ("yes", True), ("true", True), ("t", True),
    ("0", False), ("n", False), ("N", False), ("no", False), ("false", False), ("f", False),
])
def test_boolean_tokens(token, expected):
    op = _job(f"insert_job: J  job_type: CMD\ncommand: x\nmachine: m\n"
              f"date_conditions: {token}\nalarm_if_fail: {token}\n")
    assert op.job.date_conditions is expected
    assert op.job.alarm_if_fail is expected
    assert not [i for i in op.issues if i["code"] == "invalid_boolean"]


def test_unrecognised_boolean_is_flagged():
    op = _job("insert_job: J  job_type: CMD\ncommand: x\nmachine: m\ndate_conditions: maybe\n")
    assert op.job.date_conditions is False
    assert any(i["code"] == "invalid_boolean" and "date_conditions" in i["message"]
               for i in op.issues)
