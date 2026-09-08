"""Tests for JIL structural analysis modules (A1-A10)."""
from __future__ import annotations

import pytest
from datetime import datetime

from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.schema import JobRow
from autosys.models.enums import JobStatus
from autosys.analysis.migration_signals import (
    machine_concentration,
    command_analysis,
    profile_analysis,
    box_nesting_depth,
    cross_box_dependencies,
    schedule_burst_analysis,
    notification_mapping,
    log_path_analysis,
    owner_permission_mapping,
    timezone_analysis,
    run_all_structural_analyses,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'test_signals.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines(); create_all_sync(); yield; reset_engines()


def _add_job(session, name, **kw):
    defaults = dict(
        job_name=name, job_type="CMD", status=JobStatus.INACTIVE.value, owner="test",
    )
    defaults.update(kw)
    session.add(JobRow(**defaults))


class TestMachineConcentration:

    def test_groups_by_machine(self, fresh_db):
        with sync_session() as s:
            for i in range(25):
                _add_job(s, f"job_{i}", machine="big-server")
            for i in range(5):
                _add_job(s, f"small_{i}", machine="small-server")
            s.commit()

            result = machine_concentration(s)

        assert result["machines"]["big-server"]["count"] == 25
        assert result["machines"]["big-server"]["score"] == "L"
        assert result["machines"]["small-server"]["count"] == 5
        assert result["by_job"]["job_0"]["score"] == "L"


class TestCommandAnalysis:

    def test_parses_shell_script(self, fresh_db):
        with sync_session() as s:
            _add_job(s, "sh_job", command="/scripts/mktdata/fetch.sh --date %%DATE%%")
            _add_job(s, "py_job", command="/opt/app/run.py --input /data/file.csv")
            _add_job(s, "ksh_job", command="/scripts/legacy/etl.ksh")
            _add_job(s, "perl_job", command="/scripts/old/report.pl")
            s.commit()
            result = command_analysis(s)

        assert result["sh_job"]["dialect"] == "bash"
        assert result["sh_job"]["business_globals"] == ["DATE"]
        assert result["py_job"]["dialect"] == "python"
        assert result["ksh_job"]["dialect"] == "ksh"
        assert result["ksh_job"]["score"] == "L"
        assert result["perl_job"]["dialect"] == "perl"
        assert result["perl_job"]["score"] == "L"


class TestProfileAnalysis:

    def test_profiles_grouped(self, fresh_db):
        with sync_session() as s:
            _add_job(s, "j1", profile="/etc/autosys/profiles/svc_mktdata.sh")
            _add_job(s, "j2", profile="/etc/autosys/profiles/svc_mktdata.sh")
            _add_job(s, "j3", profile="/etc/autosys/profiles/svc_trade.sh")
            s.commit()
            result = profile_analysis(s)

        assert result["profiles"]["/etc/autosys/profiles/svc_mktdata.sh"]["count"] == 2
        assert result["by_job"]["j1"]["shared"] is True
        assert result["by_job"]["j3"]["shared"] is False


class TestBoxNestingDepth:

    def test_nested_boxes(self, fresh_db):
        with sync_session() as s:
            s.add(JobRow(job_name="outer", job_type="BOX", status=JobStatus.INACTIVE.value, owner="t"))
            s.add(JobRow(job_name="middle", job_type="BOX", box_name="outer", status=JobStatus.INACTIVE.value, owner="t"))
            s.add(JobRow(job_name="inner", job_type="BOX", box_name="middle", status=JobStatus.INACTIVE.value, owner="t"))
            _add_job(s, "leaf", box_name="inner")
            s.commit()
            result = box_nesting_depth(s)

        assert result["leaf"]["depth"] == 3
        assert result["leaf"]["score"] == "L"
        assert result["inner"]["depth"] == 2
        assert result["outer"]["depth"] == 0


class TestCrossBoxDependencies:

    def test_finds_cross_box_dep(self, fresh_db):
        with sync_session() as s:
            s.add(JobRow(job_name="box_a", job_type="BOX", status=JobStatus.INACTIVE.value, owner="t"))
            s.add(JobRow(job_name="box_b", job_type="BOX", status=JobStatus.INACTIVE.value, owner="t"))
            _add_job(s, "job_a1", box_name="box_a")
            _add_job(s, "job_b1", box_name="box_b", condition="success(job_a1)")
            s.commit()
            result = cross_box_dependencies(s)

        assert len(result["cross_box"]) == 1
        assert result["cross_box"][0]["job"] == "job_b1"
        assert result["cross_box"][0]["dep_box"] == "box_a"
        assert result["by_job"]["job_b1"]["has_cross_box"] is True
        assert result["by_job"]["job_b1"]["score"] == "L"


class TestScheduleBurstAnalysis:

    def test_burst_detected(self, fresh_db):
        with sync_session() as s:
            for i in range(15):
                _add_job(s, f"burst_{i}", start_times="17:30")
            _add_job(s, "lonely", start_times="03:00")
            s.commit()
            result = schedule_burst_analysis(s)

        assert result["by_time"]["17:30"]["count"] == 15
        assert result["by_job"]["burst_0"]["burst_count"] == 15
        assert result["by_job"]["burst_0"]["score"] == "M"
        assert result["by_job"]["lonely"]["score"] == "XS"


class TestNotificationMapping:

    def test_tags_notification_jobs(self, fresh_db):
        with sync_session() as s:
            _add_job(s, "alert_job", alarm_if_fail=True, notification_emailaddress="ops@bank.com")
            _add_job(s, "silent_job")
            s.commit()
            result = notification_mapping(s)

        assert "alert_job" in result
        assert result["alert_job"]["alarm_if_fail"] is True
        assert "silent_job" not in result


class TestLogPathAnalysis:

    def test_hardcoded_paths(self, fresh_db):
        with sync_session() as s:
            _add_job(s, "logged_job",
                     std_out_file="/logs/mktdata/output.out",
                     std_err_file="/logs/mktdata/error.err")
            s.commit()
            result = log_path_analysis(s)

        assert result["logged_job"]["hardcoded"] is True
        assert result["logged_job"]["score"] == "M"


class TestOwnerPermissionMapping:

    def test_complex_permissions(self, fresh_db):
        with sync_session() as s:
            _add_job(s, "perm_job", owner="svc_app", permission="gx,wx,me,mx,ge,we")
            _add_job(s, "simple_job", owner="svc_app", permission="gx")
            s.commit()
            result = owner_permission_mapping(s)

        assert result["by_job"]["perm_job"]["perm_count"] == 6
        assert result["by_job"]["perm_job"]["score"] == "M"
        assert len(result["by_owner"]["svc_app"]) == 2


class TestTimezoneAnalysis:

    def test_timezone_detected(self, fresh_db):
        with sync_session() as s:
            _add_job(s, "tz_job", timezone="US/Eastern")
            _add_job(s, "no_tz_job")
            s.commit()
            result = timezone_analysis(s)

        assert "tz_job" in result
        assert result["tz_job"]["timezone"] == "US/Eastern"
        assert "no_tz_job" not in result


class TestRunAll:

    def test_all_analyses_return_dict(self, fresh_db):
        with sync_session() as s:
            _add_job(s, "j1", machine="server1", command="/scripts/run.sh",
                     profile="/etc/profile.sh", alarm_if_fail=True,
                     std_out_file="/logs/out.log", timezone="UTC",
                     permission="gx,wx", owner="svc")
            s.commit()
            result = run_all_structural_analyses(s)

        assert "machine_concentration" in result
        assert "command_analysis" in result
        assert "profile_analysis" in result
        assert "box_nesting_depth" in result
        assert "cross_box_dependencies" in result
        assert "schedule_burst_analysis" in result
        assert "notification_mapping" in result
        assert "log_path_analysis" in result
        assert "owner_permission_mapping" in result
        assert "timezone_analysis" in result
