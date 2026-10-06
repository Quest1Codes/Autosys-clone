"""
Simulator scheduling matches AutoSys (audit SEM-04, SEM-06, SEM-07).

SEM-04  a top-level job started only by its condition never ran (nothing
        re-evaluated conditions when the job it names finished), a timed job
        whose condition was unmet at its start time lost the run, and the
        simulation force-started every top-level job, ignoring conditions
        between boxes.
SEM-06  a box inside a box was dispatched like a command: the inner box
        "succeeded" while its children never ran.
SEM-07  ON_ICE was inverted: dependents of an ON_ICE job waited forever and
        an ON_ICE child kept its box from completing. AutoSys runs them as
        if it had succeeded.
(SEM-05, several start_times a day, is in tests/test_phase4.py.)
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from autosys.models.enums import JobStatus


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 's.db'}")
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    connection.reset_engines()
    create_all_sync()
    yield connection
    connection.reset_engines()


def _load(db, tmp_path, jil):
    from autosys.parser.jil_ingest import ingest_paths
    p = Path(tmp_path) / "x.jil"
    p.write_text(jil)
    ingest_paths(db.sync_session, [p])


def _status(db):
    from autosys.db.schema import JobRow
    with db.sync_session() as s:
        return {r.job_name: JobStatus(r.status).name for r in s.scalars(select(JobRow))}


def _send(db, event, job):
    from autosys.db.repository import events
    from autosys.models.event import Event
    with db.sync_session() as s:
        events.enqueue(s, Event(event_type=event, job_name=job))
        s.commit()


def _ticks(db, ep, n, now=datetime(2026, 6, 24, 10, 0)):
    for _ in range(n):
        with db.sync_session() as s:
            ep.process_one_tick(s, now=now)
            s.commit()


def _ep():
    from autosys.scheduler.event_processor import EventProcessor
    return EventProcessor(auto_complete=True)


CMD = "job_type: CMD\ncommand: x\nmachine: localhost\n"


# --- SEM-04 --------------------------------------------------------------------

def test_condition_only_job_starts_when_its_upstream_succeeds(db, tmp_path):
    _load(db, tmp_path, f"insert_job: UP  {CMD}\ninsert_job: DOWN  {CMD}condition: s(UP)\n")
    ep = _ep()
    _ticks(db, ep, 1)
    _send(db, "STARTJOB", "UP")
    _ticks(db, ep, 4)
    assert _status(db) == {"UP": "SUCCESS", "DOWN": "SUCCESS"}


def test_condition_only_job_does_not_start_when_upstream_fails(db, tmp_path):
    _load(db, tmp_path, f"insert_job: UP  {CMD}\ninsert_job: DOWN  {CMD}condition: s(UP)\n")
    ep = _ep()
    _ticks(db, ep, 1)
    from autosys.db.schema import JobRow
    with db.sync_session() as s:
        s.get(JobRow, "UP").status = JobStatus.FAILURE.value
        s.commit()
    _ticks(db, ep, 4)
    assert _status(db)["DOWN"] == "INACTIVE"


def test_timed_job_waits_for_its_condition_instead_of_dropping_the_run(db, tmp_path):
    _load(db, tmp_path, f"insert_job: UP  {CMD}\n"
                        f"insert_job: TIMED  {CMD}condition: s(UP)\n"
                        "date_conditions: 1\ndays_of_week: all\nstart_times: \"10:00\"\n")
    ep = _ep()
    _ticks(db, ep, 3)                                   # 10:00: start time comes, UP not done
    assert _status(db)["TIMED"] == "INACTIVE"
    _send(db, "STARTJOB", "UP")
    _ticks(db, ep, 4, now=datetime(2026, 6, 24, 10, 30))
    assert _status(db)["TIMED"] == "SUCCESS"


def test_timed_job_does_not_start_before_its_time(db, tmp_path):
    _load(db, tmp_path, f"insert_job: UP  {CMD}\n"
                        f"insert_job: TIMED  {CMD}condition: s(UP)\n"
                        "date_conditions: 1\ndays_of_week: all\nstart_times: \"23:00\"\n")
    ep = _ep()
    _ticks(db, ep, 1)
    _send(db, "STARTJOB", "UP")
    _ticks(db, ep, 4)
    assert _status(db)["TIMED"] == "INACTIVE"


def test_simulation_respects_conditions_between_top_level_jobs(db, tmp_path):
    from autosys.scheduler.simulation_runner import run_simulation
    _load(db, tmp_path, f"insert_job: ROOT  {CMD}\n"
                        f"insert_job: AFTER_ROOT  {CMD}condition: s(ROOT)\n"
                        f"insert_job: NEVER  {CMD}condition: s(MISSING_JOB)\n"
                        + "".join(f"insert_job: C{i:02d}  {CMD}condition: s(C{i - 1:02d})\n"
                                  if i else f"insert_job: C00  {CMD}\n" for i in range(25)))
    with db.sync_session() as s:
        run_simulation(s, cycles=1, ticks_per_cycle=2, seed=1)
        from autosys.db.schema import JobRunRow
        ran = {r.job_name for r in s.scalars(select(JobRunRow))}
    assert "AFTER_ROOT" in ran
    assert "NEVER" not in ran                           # its upstream never runs
    # a 25-deep chain is no longer cut off at the first 2 ticks: it runs
    # until a (randomly injected) failure stops it
    chain = [f"C{i:02d}" for i in range(25)]
    assert len([c for c in chain if c in ran]) > 2


# --- SEM-06 --------------------------------------------------------------------

NESTED = ("insert_job: OUTER  job_type: BOX\n"
          "insert_job: INNER  job_type: BOX\nbox_name: OUTER\n"
          f"insert_job: LEAF  {CMD}box_name: INNER\n")


@pytest.mark.parametrize("event", ["STARTJOB", "FORCE_STARTJOB"])
def test_nested_box_runs_its_children(db, tmp_path, event):
    _load(db, tmp_path, NESTED)
    ep = _ep()
    _send(db, event, "OUTER")
    _ticks(db, ep, 6)
    assert _status(db) == {"OUTER": "SUCCESS", "INNER": "SUCCESS", "LEAF": "SUCCESS"}


# --- SEM-07 --------------------------------------------------------------------

def test_dependents_of_an_on_ice_job_run(db, tmp_path):
    from autosys.parser.condition_parser import evaluate, parse_condition
    snap = {"A": "ON_ICE"}
    assert evaluate(parse_condition("s(A)"), snap)
    assert evaluate(parse_condition("d(A)"), snap)
    assert not evaluate(parse_condition("f(A)"), snap)


def test_on_ice_child_does_not_hold_its_box(db, tmp_path):
    _load(db, tmp_path, "insert_job: B  job_type: BOX\n"
                        f"insert_job: ICED  {CMD}box_name: B\n"
                        f"insert_job: NEXT  {CMD}box_name: B\ncondition: s(ICED)\n")
    ep = _ep()
    _send(db, "JOB_ON_ICE", "ICED")
    _ticks(db, ep, 1)
    _send(db, "STARTJOB", "B")
    _ticks(db, ep, 6)
    st = _status(db)
    assert st["ICED"] == "ON_ICE" and st["NEXT"] == "SUCCESS" and st["B"] == "SUCCESS"
