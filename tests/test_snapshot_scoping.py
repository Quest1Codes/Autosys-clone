"""
Name-scoped condition snapshots (task E6).

A box start used to rebuild the status, exit-code and last-end snapshots from
the whole estate, so a dry-run simulation cycle was quadratic in estate size
(2K jobs x 20 cycles: 7 minutes). It now builds them for just the jobs its
children's conditions reference. That is only safe if a scoped snapshot is
exactly the full snapshot restricted to those names -- which is what these
tests pin, including past the IN-list chunk size and for names that don't
exist.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Generator

import pytest
from sqlalchemy.orm import Session

from autosys.db.connection import sync_session
from autosys.db.repository import _IN_CHUNK
from autosys.db.schema import JobRow, JobRunRow
from autosys.scheduler.condition_evaluator import (
    build_exitcode_snapshot, build_last_times_snapshot, build_status_snapshot,
    is_satisfied, referenced_job_names,
)

_NOW = datetime(2026, 10, 5, 12, 0, 0)
_N = _IN_CHUNK * 2 + 37  # forces three IN chunks


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'scope.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection as _conn
    _conn.reset_engines()
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=False)
    yield
    _conn.reset_engines()


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    rng = random.Random(7)
    with sync_session() as s:
        for i in range(_N):
            s.add(JobRow(
                job_name=f"j{i:05d}", job_type="CMD", command="x",
                status=rng.choice([1, 4, 5, 8, 9]),
                last_end=_NOW - timedelta(hours=rng.randint(0, 48)) if rng.random() < .7 else None,
            ))
        s.flush()
        # Several runs per job so "latest exit code" has to pick the newest.
        for i in range(0, _N, 3):
            for k in range(rng.randint(1, 3)):
                s.add(JobRunRow(
                    run_id=f"r{i}-{k}", job_name=f"j{i:05d}", status=4,
                    start_time=_NOW - timedelta(hours=10 - k),
                    exit_code=rng.choice([0, 1, 2, None]),
                ))
        s.commit()
        yield s


def _restrict(full: dict, names: set[str]) -> dict:
    return {k: v for k, v in full.items() if k in names}


@pytest.mark.parametrize("pick", [0, 1, 5, _IN_CHUNK, _N])
def test_scoped_snapshots_equal_full_snapshots_restricted(session, pick):
    rng = random.Random(pick)
    names = set(rng.sample([f"j{i:05d}" for i in range(_N)], pick))
    names |= {"does_not_exist", "j99999"}  # unknown names are simply absent

    assert build_status_snapshot(session, names) == _restrict(build_status_snapshot(session), names)
    assert build_exitcode_snapshot(session, names) == _restrict(build_exitcode_snapshot(session), names)
    assert build_last_times_snapshot(session, names) == _restrict(build_last_times_snapshot(session), names)


def test_unscoped_last_times_matches_full_orm_scan(session):
    # build_last_times_snapshot now projects two columns instead of loading
    # every ORM row; the full (names=None) result must not change.
    from autosys.db.repository import jobs as job_repo
    assert build_last_times_snapshot(session) == {
        r.job_name: r.last_end for r in job_repo.list_all(session)
    }


def test_conditions_evaluate_identically_on_scoped_snapshots(session):
    rng = random.Random(11)
    jobs = [f"j{i:05d}" for i in range(_N)]
    preds = ["s({})", "f({})", "d({})", "n({})", "t({})", "e({}) = 0",
             "exitcode({}) != 1", "s({}, 12.00)", "s({}, 01.00)"]
    full = (build_status_snapshot(session), build_exitcode_snapshot(session),
            build_last_times_snapshot(session))
    checked = 0
    for _ in range(400):
        terms = [rng.choice(preds).format(rng.choice(jobs + ["ghost_job"]))
                 for _ in range(rng.randint(1, 4))]
        cond = terms[0]
        for t in terms[1:]:
            cond += rng.choice([" & ", " | "]) + t
        refs = referenced_job_names(cond)
        scoped = (build_status_snapshot(session, refs), build_exitcode_snapshot(session, refs),
                  build_last_times_snapshot(session, refs))
        want = is_satisfied(cond, full[0], job_exitcodes=full[1], job_last_times=full[2], now=_NOW)
        got = is_satisfied(cond, scoped[0], job_exitcodes=scoped[1], job_last_times=scoped[2], now=_NOW)
        assert got == want, cond
        checked += 1
    assert checked == 400


def test_unparseable_condition_is_false_with_empty_scope(session):
    cond = "s(j00001) &&& ("
    assert referenced_job_names(cond) == set()
    full = build_status_snapshot(session)
    assert is_satisfied(cond, full) is False
    assert is_satisfied(cond, build_status_snapshot(session, set())) is False
