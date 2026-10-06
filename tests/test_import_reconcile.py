"""
Imports stay consistent across requests, renames and newer exports
(audit ING-03, ING-04, ING-05, ING-06, ING-08, ING-12, ING-13).

ING-06/12  a child whose box arrives in a later request or run was left
           unboxed for good; every import now links waiting children.
ING-04     rename_job rewrote other jobs' conditions by substring
           (s(job_ab) became s(job_xb)) and skipped box_success/box_failure.
ING-13     a job with an alarm or captured output could not be deleted, and
           delete_box left nested grandchildren behind.
ING-05     importing a newer export kept stale definitions and deleted jobs;
           --replace-estate now replaces the estate and archives removals.
ING-03/08  PostgreSQL only: rename needed superuser; concurrent imports
           deadlocked. Those run when AUTOSYS_TEST_PG_URL is set.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'r.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    connection.reset_engines()
    create_all_sync()
    yield connection
    connection.reset_engines()


def _jobs(db):
    from autosys.db.schema import JobRow
    with db.sync_session() as s:
        return {r.job_name: (r.box_name, r.condition, r.box_success) for r in s.scalars(select(JobRow))}


def _write(tmp_path: Path, name: str, jil: str) -> Path:
    p = tmp_path / name
    p.write_text(jil)
    return p


def _ingest(db, paths, **kw):
    from autosys.parser.jil_ingest import ingest_paths
    return ingest_paths(db.sync_session, list(paths), **kw)


CHILD = "insert_job: CHILD  job_type: CMD\nbox_name: BOX1\ncommand: x\nmachine: m\n"
BOX = "insert_job: BOX1  job_type: BOX\n"


# --- ING-06 / ING-12 ---------------------------------------------------------

def test_box_in_a_later_api_request_links_the_child(db):
    from autosys.app_server.main import create_app
    with TestClient(create_app(start_eps=False)) as c:
        assert c.post("/api/v1/jil/import", json={"content": CHILD}).status_code == 200
        assert _jobs(db)["CHILD"][0] is None                 # waiting for its box
        assert c.post("/api/v1/jil/import", json={"content": BOX}).status_code == 200
    assert _jobs(db)["CHILD"][0] == "BOX1"


def test_box_in_a_later_cli_run_links_the_child(db, tmp_path):
    _ingest(db, [_write(tmp_path, "a.jil", CHILD)])          # resumed run: a fresh pending list
    _ingest(db, [_write(tmp_path, "b.jil", BOX)])
    assert _jobs(db)["CHILD"][0] == "BOX1"
    from autosys.db.schema import JilStanzaRow
    with db.sync_session() as s:
        issues = json.loads(s.scalars(select(JilStanzaRow.issues_json)
                                      .where(JilStanzaRow.object_name == "CHILD")).one())
    assert [i["code"] for i in issues][-1] == "box_linked"


# --- ING-04 ------------------------------------------------------------------

def test_rename_touches_whole_job_names_only():
    from autosys.analysis.condition_refs import rename_job_refs as r
    assert r("s(job_ab) & s(job_a)", "job_a", "job_x") == "s(job_ab) & s(job_x)"
    assert r("success(job_a, 01.00) | e(job_a) = 0", "job_a", "N") == "success(N, 01.00) | e(N) = 0"
    assert r("s(job_a^PRD) & v(job_a) = \"1\"", "job_a", "N") == "s(job_a^PRD) & v(job_a) = \"1\""
    assert r(None, "a", "b") is None


def test_rename_job_updates_conditions_and_box_success(db, tmp_path):
    _ingest(db, [_write(tmp_path, "r.jil",
                        "insert_job: job_a  job_type: CMD\ncommand: x\nmachine: m\n\n"
                        "insert_job: job_ab  job_type: CMD\ncommand: x\nmachine: m\n\n"
                        "insert_job: B  job_type: BOX\nbox_success: s(job_a)\n\n"
                        "insert_job: D  job_type: CMD\ncommand: x\nmachine: m\n"
                        "condition: s(job_ab) & s(job_a)\n\n"
                        "rename_job: job_a\nnew_name: job_x\n")])
    jobs = _jobs(db)
    assert "job_a" not in jobs and "job_x" in jobs
    assert jobs["D"][1] == "s(job_ab) & s(job_x)"
    assert jobs["B"][2] == "s(job_x)"


# --- ING-13 ------------------------------------------------------------------

def _add_alarm(db, job):
    from autosys.db.schema import AlarmRow, JobRunRow
    with db.sync_session() as s:
        run = JobRunRow(run_id=str(uuid.uuid4()), job_name=job, status=5)
        s.add(run)
        s.flush()
        s.add(AlarmRow(alarm_id=str(uuid.uuid4()), job_name=job, run_id=run.run_id,
                       alarm_type="JOBFAILURE", message="m", raised_at=datetime.now(timezone.utc)))
        s.commit()


def test_job_with_an_alarm_can_be_deleted(db, tmp_path):
    _ingest(db, [_write(tmp_path, "a.jil", "insert_job: A  job_type: CMD\ncommand: x\nmachine: m\n")])
    _add_alarm(db, "A")
    _ingest(db, [_write(tmp_path, "d.jil", "delete_job: A\n")])
    assert "A" not in _jobs(db)


def test_delete_box_removes_every_nesting_level(db, tmp_path):
    _ingest(db, [_write(tmp_path, "n.jil",
                        "insert_job: OUTER  job_type: BOX\n\n"
                        "insert_job: INNER  job_type: BOX\nbox_name: OUTER\n\n"
                        "insert_job: LEAF  job_type: CMD\nbox_name: INNER\ncommand: x\nmachine: m\n")])
    _add_alarm(db, "LEAF")
    _ingest(db, [_write(tmp_path, "d.jil", "delete_box: OUTER\n")])
    assert _jobs(db) == {}


# --- ING-05 ------------------------------------------------------------------

V1 = ("insert_job: KEEP  job_type: CMD\ncommand: old\nmachine: m\n\n"
      "insert_job: GONE  job_type: CMD\ncommand: x\nmachine: m\n")
V2 = "insert_job: KEEP  job_type: CMD\ncommand: new\nmachine: m\n"


def _command(db, name):
    from autosys.db.schema import JobRow
    with db.sync_session() as s:
        return s.get(JobRow, name).command


def test_default_import_of_a_newer_export_keeps_the_old_estate(db, tmp_path):
    _ingest(db, [_write(tmp_path, "v1.jil", V1)])
    _ingest(db, [_write(tmp_path, "v2.jil", V2)])
    assert set(_jobs(db)) == {"KEEP", "GONE"} and _command(db, "KEEP") == "old"


def test_replace_estate_takes_new_definitions_and_archives_removals(db, tmp_path):
    _ingest(db, [_write(tmp_path, "v1.jil", V1)])
    _add_alarm(db, "GONE")
    summary = _ingest(db, [_write(tmp_path, "v2.jil", V2)], replace_estate=True)
    assert set(_jobs(db)) == {"KEEP"} and _command(db, "KEEP") == "new"
    assert summary.removed_jobs == ["GONE"]
    from autosys.db.schema import JilFileRow, JilStanzaRow
    with db.sync_session() as s:
        row = s.execute(select(JilStanzaRow, JilFileRow.path)
                        .join(JilFileRow, JilFileRow.file_id == JilStanzaRow.file_id)
                        .where(JilStanzaRow.object_name == "GONE",
                               JilStanzaRow.directive == "delete_job")).one()
    assert row.path.startswith("reconcile:")
    assert "insert_job: GONE" in row[0].raw_text                  # last definition kept
    assert json.loads(row[0].issues_json)[0]["code"] == "removed_in_new_export"


def test_replace_estate_keeps_a_job_that_failed_to_store(db, tmp_path, monkeypatch):
    _ingest(db, [_write(tmp_path, "v1.jil", V1)])
    import autosys.parser.jil_ingest as ingest
    real = ingest.apply_operation

    def refuse_gone(session, op, *a, **kw):
        if getattr(getattr(op, "job", None), "job_name", None) == "GONE":
            raise RuntimeError("simulated database rejection")
        return real(session, op, *a, **kw)

    monkeypatch.setattr(ingest, "apply_operation", refuse_gone)
    summary = _ingest(db, [_write(tmp_path, "v2.jil", V1)], replace_estate=True)
    assert summary.removed_jobs == [] and "GONE" in _jobs(db)


def test_replace_estate_with_no_jobs_removes_nothing(db, tmp_path):
    _ingest(db, [_write(tmp_path, "v1.jil", V1)])
    summary = _ingest(db, [_write(tmp_path, "empty.jil", "/* nothing */\n")], replace_estate=True)
    assert summary.removed_jobs == [] and set(_jobs(db)) == {"KEEP", "GONE"}


# --- PostgreSQL: ING-03, ING-08 -------------------------------------------------

PG_URL = os.environ.get("AUTOSYS_TEST_PG_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="AUTOSYS_TEST_PG_URL not set")


@pytest.fixture()
def pg(monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", PG_URL)
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    from autosys.db.schema import Base
    connection.reset_engines()
    Base.metadata.drop_all(connection.get_sync_engine())
    create_all_sync()
    yield connection
    connection.reset_engines()


@needs_pg
def test_rename_works_on_postgres_without_superuser(pg, tmp_path):
    _ingest(pg, [_write(tmp_path, "r.jil",
                        "insert_job: job_a  job_type: CMD\ncommand: x\nmachine: m\n\n"
                        "insert_job: D  job_type: CMD\ncommand: x\nmachine: m\ncondition: s(job_a)\n\n"
                        "rename_job: job_a\nnew_name: job_x\n")])
    jobs = _jobs(pg)
    assert "job_x" in jobs and "job_a" not in jobs and jobs["D"][1] == "s(job_x)"


@needs_pg
def test_concurrent_imports_on_postgres_do_not_deadlock(pg, tmp_path):
    n = 300
    jil = "".join(f"insert_job: SHARED_{i:04d}  job_type: CMD\ncommand: x\nmachine: m\n"
                  f"condition: s(SHARED_{(i + 1) % n:04d})\n\n" for i in range(n))
    files = [_write(tmp_path, f"t{k}.jil", jil) for k in range(2)]
    summaries = [None, None]

    def run(k):
        summaries[k] = _ingest(pg, [files[k]], duplicates="last")

    threads = [threading.Thread(target=run, args=(k,)) for k in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert all(s is not None for s in summaries)
    for s in summaries:
        assert s.dispositions.get("ARCHIVE_ONLY", 0) == 0, s.dispositions
    assert len(_jobs(pg)) == n
