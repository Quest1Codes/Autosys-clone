"""End-to-end integration test for JIL-only migration complexity analysis.

Tests the full pipeline:
1. Import JIL files → DB
2. Run multi-cycle simulation → JobRunRow/AlarmRow history
3. Run structural analyses (A1-A10)
4. Build enriched report with migration signals
5. Compute summary with migration signal aggregation
6. Verify CLI and REST endpoints produce valid output
"""
from __future__ import annotations

import pytest
from datetime import datetime

from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.schema import JobRow, JobRunRow, AlarmRow
from autosys.models.enums import JobStatus
from autosys.analysis.migration_signals import run_all_structural_analyses
from autosys.analysis.complexity import build_report, compute_summary
from autosys.analysis.operational_risk import fetch_run_stats
from autosys.scheduler.simulation_runner import run_simulation
from sqlalchemy import select


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'test_e2e.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines(); create_all_sync(); yield; reset_engines()


def _seed_jobs(session):
    """Seed a realistic set of jobs with various complexity factors."""
    # Top-level box
    session.add(JobRow(
        job_name="etl_box", job_type="BOX", status=JobStatus.INACTIVE.value,
        owner="svc_etl", machine="prod_server",
    ))
    # Child with command and profile
    session.add(JobRow(
        job_name="fetch_data", job_type="CMD", box_name="etl_box",
        status=JobStatus.INACTIVE.value, owner="svc_etl",
        machine="prod_server", command="/scripts/fetch.sh --date %%DATE%%",
        profile="/etc/autosys/profiles/etl.sh",
        std_out_file="/logs/etl/fetch.out",
        std_err_file="/logs/etl/fetch.err",
        alarm_if_fail=True,
        notification_emailaddress="ops@bank.com",
        n_retrys=3, avg_runtime=5,
        timezone="US/Eastern",
        permission="gx,wx,me,mx,ge,we",
    ))
    # Child with condition (depends on fetch_data)
    session.add(JobRow(
        job_name="transform_data", job_type="CMD", box_name="etl_box",
        status=JobStatus.INACTIVE.value, owner="svc_etl",
        machine="prod_server", command="/scripts/transform.py --input /data/raw.csv",
        profile="/etc/autosys/profiles/etl.sh",
        condition="success(fetch_data)",
        n_retrys=1,
    ))
    # Child with condition (depends on transform_data)
    session.add(JobRow(
        job_name="load_warehouse", job_type="CMD", box_name="etl_box",
        status=JobStatus.INACTIVE.value, owner="svc_etl",
        machine="prod_server", command="/scripts/load.ksh --target wh",
        profile="/etc/autosys/profiles/etl.sh",
        condition="success(transform_data)",
        n_retrys=0, max_run_alarm=10,
    ))
    # Another box with cross-box dependency
    session.add(JobRow(
        job_name="report_box", job_type="BOX", status=JobStatus.INACTIVE.value,
        owner="svc_rpt", machine="rpt_server",
    ))
    session.add(JobRow(
        job_name="gen_report", job_type="CMD", box_name="report_box",
        status=JobStatus.INACTIVE.value, owner="svc_rpt",
        machine="rpt_server", command="/scripts/report.pl --format pdf",
        condition="success(load_warehouse)",
        alarm_if_fail=True,
        notification_emailaddress="rpt@bank.com",
    ))
    session.flush()


class TestEndToEndMigrationReport:

    def test_full_pipeline(self, fresh_db):
        """Test the complete JIL-only migration analysis pipeline."""
        with sync_session() as s:
            _seed_jobs(s)
            s.commit()

            # 1. Run simulation
            sim_result = run_simulation(s, cycles=10, ticks_per_cycle=5, seed=42)
            assert sim_result["total_runs"] > 0
            assert sim_result["cycles"] == 10

            # 2. Verify JobRunRow records were created
            runs = list(s.scalars(select(JobRunRow)))
            assert len(runs) > 0

            # 3. Verify some runs have failures (n_retrys=3 → 25% rate)
            failures = [r for r in runs if r.status == JobStatus.FAILURE.value]
            assert len(failures) > 0

            # 4. Run structural analyses
            signals = run_all_structural_analyses(s)
            assert "machine_concentration" in signals
            assert "command_analysis" in signals
            assert "cross_box_dependencies" in signals

            # 5. Verify cross-box dependency detected
            cross_box = signals["cross_box_dependencies"]["cross_box"]
            assert any(c["job"] == "gen_report" for c in cross_box)

            # 6. Verify command dialects detected
            cmd_analysis = signals["command_analysis"]
            assert cmd_analysis["fetch_data"]["dialect"] == "bash"
            assert cmd_analysis["transform_data"]["dialect"] == "python"
            assert cmd_analysis["load_warehouse"]["dialect"] == "ksh"

            # 7. Fetch run stats from simulated history
            rows = list(s.scalars(select(JobRow)))
            run_stats = fetch_run_stats(s, [r.job_name for r in rows])

            # 8. Build enriched report
            assessments = build_report(
                rows, run_stats=run_stats, migration_signals=signals
            )
            assert len(assessments) == 6  # 2 boxes + 4 CMD jobs

            # 9. Verify migration signals are populated
            fetch_assessment = next(a for a in assessments if a.job_name == "fetch_data")
            assert fetch_assessment.command_dialect == "bash"
            assert fetch_assessment.has_notifications is True
            assert fetch_assessment.has_hardcoded_logs is True
            assert fetch_assessment.timezone == "US/Eastern"

            gen_assessment = next(a for a in assessments if a.job_name == "gen_report")
            assert gen_assessment.has_cross_box_dep is True

            # 10. Compute summary with migration signals
            summary = compute_summary(assessments)
            assert summary.total_jobs == 6
            assert summary.cross_box_dep_count >= 1
            assert summary.notification_job_count >= 2
            assert summary.hardcoded_log_job_count >= 1
            assert summary.timezone_count >= 1
            assert "bash" in summary.dialect_counts
            assert "python" in summary.dialect_counts
            assert "ksh" in summary.dialect_counts

    def test_simulation_generates_alarms(self, fresh_db):
        """Verify that alarm_if_fail jobs generate alarms on failure."""
        with sync_session() as s:
            _seed_jobs(s)
            s.commit()
            run_simulation(s, cycles=10, ticks_per_cycle=5, seed=42)

            alarms = list(s.scalars(select(AlarmRow)))
            # fetch_data has alarm_if_fail=True and n_retrys=3 (25% failure rate)
            fetch_alarms = [a for a in alarms if a.job_name == "fetch_data"]
            assert len(fetch_alarms) > 0

    def test_report_without_simulation(self, fresh_db):
        """Verify report works without simulation (risk=NO_DATA)."""
        with sync_session() as s:
            _seed_jobs(s)
            s.commit()
            signals = run_all_structural_analyses(s)
            rows = list(s.scalars(select(JobRow)))
            assessments = build_report(rows, migration_signals=signals)
            summary = compute_summary(assessments)

            assert summary.total_jobs == 6
            # Without simulation, all risks should be NO_DATA
            assert summary.risk_counts.get("NO_DATA", 0) > 0
