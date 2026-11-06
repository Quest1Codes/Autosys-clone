"""
T16: disposition rules, read from class + traits + structural signals.

Most tests build a tiny manifest directly through job_repo.upsert (real
ORM, real validation, no HTTP layer needed since disposition reads stored
rows, not raw text) -- faster than the full import pipeline and sufficient
since classification itself is already covered end-to-end in
test_classification.py. One end-to-end test at the bottom goes through the
real /api/v1/jil/import endpoint on the ported synthetic fixture, to catch
anything a direct upsert might paper over.
"""
from __future__ import annotations

import pytest

from autosys.analysis.disposition import TargetAnswers, build_manifest, is_windows_shaped
from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo, calendars as calendar_repo
from autosys.db.schema import CalendarRow
from autosys.models.job import parse_job


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_disposition.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection as _conn
    _conn.reset_engines()
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=False)
    yield
    _conn.reset_engines()


def _seed(jobs: list[dict]):
    with sync_session() as s:
        for j in jobs:
            job_repo.upsert(s, parse_job(j))
        s.commit()


def _manifest(answers: TargetAnswers | None = None) -> dict[str, "JobDisposition"]:
    with sync_session() as s:
        rows = job_repo.list_all(s)
        return {d.job_name: d for d in build_manifest(rows, s, answers)}


class TestBoxMapping:
    def test_empty_box_is_none(self):
        _seed([{"job_name": "empty_box", "job_type": "BOX"}])
        d = _manifest()["empty_box"]
        assert (d.best, d.worst, d.rule_id) == ("NONE", "NONE", "D-BOX-EMPTY")

    def test_box_with_children_is_mech(self):
        _seed([
            {"job_name": "box1", "job_type": "BOX"},
            {"job_name": "child", "job_type": "CMD", "command": "/bin/echo hi", "machine": "m1", "box_name": "box1"},
        ])
        d = _manifest()["box1"]
        assert (d.best, d.worst, d.rule_id) == ("MECH", "MECH", "D-BOX")


class TestFixedClasses:
    def test_no_op(self):
        _seed([{"job_name": "a", "job_type": "CMD", "command": "sleep 5", "machine": "m1"}])
        d = _manifest()["a"]
        assert (d.best, d.worst) == ("NONE", "NONE")

    def test_scheduler_control_is_redesign(self):
        _seed([{"job_name": "a", "job_type": "CMD", "command": "sendevent -E FORCE_STARTJOB -J x", "machine": "m1"}])
        d = _manifest()["a"]
        assert (d.best, d.worst, d.rule_id) == ("REDESIGN", "REDESIGN", "D-SCHED")

    def test_direct_mapping_classes_are_mech(self):
        _seed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"}])
        d = _manifest()["a"]
        assert (d.best, d.worst, d.rule_id) == ("MECH", "MECH", "D-DIRECT")

    def test_unclassified_is_judg_never_mech_or_redesign(self):
        # The explicit anti-rule in the research (Part D §5.3): no rule may
        # turn unclassified into REDESIGN or MECH.
        _seed([{"job_name": "a", "job_type": "CMD", "command": "/opt/frobco/bin/frobco_loader", "machine": "m1"}])
        d = _manifest()["a"]
        assert d.class_ == "unclassified"
        assert (d.best, d.worst, d.rule_id) == ("JUDG", "JUDG", "D-UNCLASS")

    def test_honest_gap_classes_land_on_judg_not_a_guess(self):
        _seed([
            {"job_name": "ws", "job_type": "HTTP", "machine": "m1"},
            {"job_name": "db", "job_type": "SQL", "command": "select 1", "machine": "m1"},
        ])
        m = _manifest()
        assert (m["ws"].best, m["ws"].worst) == ("JUDG", "JUDG")
        assert (m["db"].best, m["db"].worst) == ("JUDG", "JUDG")


class TestConditionalVendorDisposition:
    """D-VENDOR/D-NATIVE-VENDOR: the D3/D4 fix. Unsure prints both
    scenarios, never averaged, never a single picked number."""

    def test_unsure_prints_both_scenarios(self):
        _seed([{"job_name": "a", "job_type": "SAP", "machine": "m1"}])
        d = _manifest()["a"]
        assert (d.best, d.worst) == ("MECH", "REDESIGN")

    def test_callable_answer_gives_mech_both_sides(self):
        _seed([{"job_name": "a", "job_type": "SAP", "machine": "m1"}])
        answers = TargetAnswers(vendor_runtime={"erp_native_sap": "callable"})
        d = _manifest(answers)["a"]
        assert (d.best, d.worst) == ("MECH", "MECH")

    def test_retired_answer_gives_redesign_both_sides(self):
        _seed([{"job_name": "a", "job_type": "SAP", "machine": "m1"}])
        answers = TargetAnswers(vendor_runtime={"erp_native_sap": "retired"})
        d = _manifest(answers)["a"]
        assert (d.best, d.worst) == ("REDESIGN", "REDESIGN")

    def test_answer_is_keyed_by_class_not_by_job(self):
        # Two different classes, two different answers, in one estate.
        _seed([
            {"job_name": "sap1", "job_type": "SAP", "machine": "m1"},
            {"job_name": "pmcmd1", "job_type": "CMD", "command": "pmcmd startworkflow wf1", "machine": "m1"},
        ])
        answers = TargetAnswers(vendor_runtime={"erp_native_sap": "callable", "etl_informatica": "retired"})
        m = _manifest(answers)
        assert (m["sap1"].best, m["sap1"].worst) == ("MECH", "MECH")
        assert (m["pmcmd1"].best, m["pmcmd1"].worst) == ("REDESIGN", "REDESIGN")


class TestStructuralOverridesOnlyWorsen:
    def test_or_condition_forces_judg(self):
        _seed([
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"},
            {"job_name": "b", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1", "condition": "s(a) | f(a)"},
        ])
        d = _manifest()["b"]
        assert d.worst == "JUDG"
        assert "D-COND-OR" in " ".join(d.notes)

    def test_cross_box_condition_forces_judg(self):
        _seed([
            {"job_name": "box1", "job_type": "BOX"},
            {"job_name": "box2", "job_type": "BOX"},
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1", "box_name": "box1"},
            {"job_name": "b", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1",
             "box_name": "box2", "condition": "s(a)"},
        ])
        d = _manifest()["b"]
        assert d.worst == "JUDG"
        assert "D-XBOX" in " ".join(d.notes)

    def test_dangling_reference_forces_judg(self):
        _seed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1",
                 "condition": "s(never_defined)"}])
        d = _manifest()["a"]
        assert d.worst == "JUDG"
        assert "D-DANGLE" in " ".join(d.notes)

    def test_undefined_calendar_forces_judg(self):
        with sync_session() as s:
            calendar_repo.upsert(s, CalendarRow(calendar_name="REAL_CAL", dates_json="[]"))
            s.commit()
        _seed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1",
                 "run_calendar": "GHOST_CAL"}])
        d = _manifest()["a"]
        assert d.worst == "JUDG"
        assert "D-CAL" in " ".join(d.notes)

    def test_defined_calendar_does_not_force_judg(self):
        with sync_session() as s:
            calendar_repo.upsert(s, CalendarRow(calendar_name="REAL_CAL", dates_json="[]"))
            s.commit()
        _seed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1",
                 "run_calendar": "REAL_CAL"}])
        d = _manifest()["a"]
        assert (d.best, d.worst) == ("MECH", "MECH")

    def test_env_var_trait_forces_judg(self):
        _seed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh $$BATCH_DATE", "machine": "m1"}])
        d = _manifest()["a"]
        assert d.worst == "JUDG"
        assert "D-ENV" in " ".join(d.notes)

    def test_notrunning_condition_forces_redesign(self):
        _seed([
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"},
            {"job_name": "b", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1", "condition": "n(a)"},
        ])
        d = _manifest()["b"]
        assert (d.best, d.worst, d.rule_id) == ("REDESIGN", "REDESIGN", "D-NONRUN")

    def test_overrides_never_improve_a_disposition(self):
        # A REDESIGN-base job with an OR condition must stay REDESIGN, not
        # drop to JUDG -- overrides only ever worsen.
        _seed([{"job_name": "a", "job_type": "CMD", "command": "sendevent -E X", "machine": "m1",
                 "condition": "s(a) | f(a)"}])
        d = _manifest()["a"]
        assert (d.best, d.worst) == ("REDESIGN", "REDESIGN")


class TestWindowsAsAHostTrait:
    """Q5's fix: Windows-ness applies independently of class, and the
    stricter of the two answers wins."""

    def test_is_windows_shaped_by_extension(self):
        assert is_windows_shaped('"C:\\batch\\run.bat" arg1')
        assert is_windows_shaped("/opt/tools/run.exe")
        assert not is_windows_shaped("/opt/tools/run.sh")

    def test_is_windows_shaped_by_drive_path(self):
        assert is_windows_shaped("D:\\scripts\\night.cmd")
        assert is_windows_shaped("\\\\fileserver\\share\\run.cmd")

    def test_windows_shaped_direct_class_becomes_judg_when_unsure(self):
        _seed([{"job_name": "a", "job_type": "CMD", "command": "C:\\batch\\run.bat", "machine": "winhost"}])
        d = _manifest()["a"]
        assert d.worst == "JUDG"
        assert "D-WIN" in " ".join(d.notes)

    def test_windows_shaped_vendor_class_keeps_both_scenarios_and_adds_judg(self):
        # A SAP job that's also Windows-shaped: the vendor-conditional
        # answer and the Windows answer both apply; stricter wins on each
        # side. Unsure vendor runtime already gives (MECH, REDESIGN); the
        # Windows override can only push the MECH side to at least JUDG.
        _seed([{"job_name": "a", "job_type": "SAP", "command": "C:\\sap\\run.exe", "machine": "winhost"}])
        d = _manifest()["a"]
        assert d.best == "JUDG"  # was MECH, Windows override worsens it
        assert d.worst == "REDESIGN"  # already worse than JUDG, unaffected

    def test_keep_windows_host_plan_does_not_force_judg(self):
        _seed([{"job_name": "a", "job_type": "CMD", "command": "C:\\batch\\run.bat", "machine": "winhost"}])
        answers = TargetAnswers(windows_host_plan="keep")
        d = _manifest(answers)["a"]
        assert (d.best, d.worst) == ("MECH", "MECH")


class TestEndToEndThroughRealImport:
    """One check through the real HTTP import path, not direct upsert --
    catches anything a hand-built row might paper over (extra_attrs_json
    round-tripping, multi-line conditions, etc.)."""

    def test_synthetic_fixture_builds_a_full_manifest_with_no_crash(self):
        import os
        from fastapi.testclient import TestClient
        from autosys.app_server.main import create_app

        path = os.path.join(os.path.dirname(__file__), "fixtures", "jil", "classifier_synthetic_commands.jil")
        with TestClient(create_app(start_eps=False), raise_server_exceptions=True) as c:
            r = c.post("/api/v1/jil/import", json={"content": open(path).read()})
            assert r.status_code == 200 and r.json()["success"]

        with sync_session() as s:
            rows = job_repo.list_all(s)
            manifest = build_manifest(rows, s)
        assert len(manifest) == len(rows)
        for d in manifest:
            assert d.best in ("NONE", "MECH", "JUDG", "REDESIGN")
            assert d.worst in ("NONE", "MECH", "JUDG", "REDESIGN")
            # best is never worse than worst (the ordering must hold for
            # every one of the 119 jobs, not just a hand-picked case).
            from autosys.analysis.disposition import _RANK
            assert _RANK[d.best] <= _RANK[d.worst], d
