"""
Phase 6 — Calendar CLI Expansion tests.

Tests for all autocal subcommands:
  create, add (--date, --range, --day-of-week), remove, show, delete, import, export
Plus repository methods: add_date, add_dates, remove_date
"""

from __future__ import annotations

import json
import textwrap
from click.testing import CliRunner

import pytest

from autosys.cli.main import autosys
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.repository import CalendarRepository
from autosys.db.schema import CalendarRow


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    reset_engines()
    with sync_session() as session:
        create_all_sync(session)
    yield
    reset_engines()


_cal_repo = CalendarRepository()


def _run_cli(*args) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(autosys, ["autocal", *args])
    return result.exit_code, result.output


def _get_dates(name: str) -> list[str]:
    with sync_session() as session:
        row = _cal_repo.get(session, name)
        if row is None:
            return []
        return json.loads(row.dates_json) if row.dates_json else []


# ===========================================================================
# 1. create
# ===========================================================================

class TestCreate:

    def test_create_empty_calendar(self):
        code, out = _run_cli("create", "my_cal")
        assert code == 0
        assert "Created" in out
        assert _get_dates("my_cal") == []

    def test_create_with_description(self):
        code, out = _run_cli("create", "desc_cal", "--description", "Test calendar")
        assert code == 0
        with sync_session() as session:
            row = _cal_repo.get(session, "desc_cal")
            assert row.description == "Test calendar"

    def test_create_duplicate_fails(self):
        _run_cli("create", "dup_cal")
        code, out = _run_cli("create", "dup_cal")
        assert code == 1
        assert "already exists" in out


# ===========================================================================
# 2. add
# ===========================================================================

class TestAdd:

    def test_add_single_date(self):
        _run_cli("create", "cal1")
        code, out = _run_cli("add", "cal1", "--date", "2025-01-15")
        assert code == 0
        assert "Added" in out
        assert "2025-01-15" in _get_dates("cal1")

    def test_add_date_range(self):
        _run_cli("create", "cal2")
        code, out = _run_cli("add", "cal2", "--range", "2025-01-01", "2025-01-05")
        assert code == 0
        dates = _get_dates("cal2")
        assert "2025-01-01" in dates
        assert "2025-01-05" in dates
        assert len(dates) == 5

    def test_add_day_of_week(self):
        _run_cli("create", "cal3")
        code, out = _run_cli("add", "cal3", "--day-of-week", "mo")
        assert code == 0
        dates = _get_dates("cal3")
        # All Mondays in the current year
        from datetime import date
        year = date.today().year
        jan1 = date(year, 1, 1)
        dec31 = date(year, 12, 31)
        expected_mondays = sum(
            1 for d in range((dec31 - jan1).days + 1)
            if (jan1 + __import__("datetime").timedelta(days=d)).weekday() == 0
        )
        assert len(dates) == expected_mondays

    def test_add_multiple_day_of_week(self):
        _run_cli("create", "cal4")
        code, out = _run_cli("add", "cal4", "--day-of-week", "mo,tu,we,th,fr")
        assert code == 0
        dates = _get_dates("cal4")
        assert len(dates) > 200  # weekdays in a year

    def test_add_no_option_fails(self):
        _run_cli("create", "cal5")
        code, out = _run_cli("add", "cal5")
        assert code == 1
        assert "Must specify" in out

    def test_add_to_nonexistent_fails(self):
        code, out = _run_cli("add", "nope", "--date", "2025-01-01")
        assert code == 1
        assert "not found" in out

    def test_add_duplicate_date_not_readded(self):
        _run_cli("create", "cal6")
        _run_cli("add", "cal6", "--date", "2025-01-01")
        code, out = _run_cli("add", "cal6", "--date", "2025-01-01")
        assert code == 0
        assert "0 date" in out  # 0 new dates added
        assert len(_get_dates("cal6")) == 1

    def test_add_invalid_dow_fails(self):
        _run_cli("create", "cal7")
        code, out = _run_cli("add", "cal7", "--day-of-week", "xx")
        assert code == 1
        assert "Invalid" in out


# ===========================================================================
# 3. remove
# ===========================================================================

class TestRemove:

    def test_remove_existing_date(self):
        _run_cli("create", "rem1")
        _run_cli("add", "rem1", "--date", "2025-03-15")
        code, out = _run_cli("remove", "rem1", "--date", "2025-03-15")
        assert code == 0
        assert "Removed" in out
        assert "2025-03-15" not in _get_dates("rem1")

    def test_remove_nonexistent_date(self):
        _run_cli("create", "rem2")
        _run_cli("add", "rem2", "--date", "2025-01-01")
        code, out = _run_cli("remove", "rem2", "--date", "2025-12-31")
        assert code == 0
        assert "not found" in out

    def test_remove_from_nonexistent_calendar(self):
        code, out = _run_cli("remove", "nope", "--date", "2025-01-01")
        assert code == 1
        assert "not found" in out


# ===========================================================================
# 4. show
# ===========================================================================

class TestShow:

    def test_show_with_dates(self):
        _run_cli("create", "show1", "-d", "Show test")
        _run_cli("add", "show1", "--date", "2025-06-15")
        _run_cli("add", "show1", "--date", "2025-07-20")
        code, out = _run_cli("show", "show1")
        assert code == 0
        assert "show1" in out
        assert "2025-06-15" in out
        assert "2025-07-20" in out
        assert "Show test" in out

    def test_show_empty_calendar(self):
        _run_cli("create", "show2")
        code, out = _run_cli("show", "show2")
        assert code == 0
        assert "No dates" in out

    def test_show_nonexistent_fails(self):
        code, out = _run_cli("show", "nope")
        assert code == 1
        assert "not found" in out


# ===========================================================================
# 5. delete
# ===========================================================================

class TestDelete:

    def test_delete_calendar(self):
        _run_cli("create", "del1")
        code, out = _run_cli("delete", "del1", "--yes")
        assert code == 0
        assert "Deleted" in out
        with sync_session() as session:
            assert _cal_repo.get(session, "del1") is None

    def test_delete_nonexistent_fails(self):
        code, out = _run_cli("delete", "nope", "--yes")
        assert code == 1
        assert "not found" in out


# ===========================================================================
# 6. import
# ===========================================================================

class TestImport:

    def test_import_cal_file(self, tmp_path):
        cal_file = tmp_path / "test_cal.cal"
        cal_file.write_text(textwrap.dedent("""
            # Test calendar
            2025-01-01  # New Year
            2025-07-04  # Independence Day
            2025-12-25  # Christmas
        """))
        code, out = _run_cli("import", str(cal_file))
        assert code == 0
        assert "Imported" in out
        dates = _get_dates("test_cal")
        assert "2025-01-01" in dates
        assert "2025-07-04" in dates
        assert "2025-12-25" in dates

    def test_import_with_explicit_name(self, tmp_path):
        cal_file = tmp_path / "holidays.cal"
        cal_file.write_text("2025-01-01\n2025-12-25\n")
        code, out = _run_cli("import", str(cal_file), "--name", "my_holidays")
        assert code == 0
        assert "my_holidays" in out
        assert len(_get_dates("my_holidays")) == 2

    def test_import_merges_existing(self, tmp_path):
        _run_cli("create", "merge_cal")
        _run_cli("add", "merge_cal", "--date", "2025-01-01")
        cal_file = tmp_path / "merge_cal.cal"
        cal_file.write_text("2025-01-01\n2025-06-15\n")
        code, out = _run_cli("import", str(cal_file))
        assert code == 0
        dates = _get_dates("merge_cal")
        assert "2025-01-01" in dates
        assert "2025-06-15" in dates
        assert len(dates) == 2  # deduplicated


# ===========================================================================
# 7. export
# ===========================================================================

class TestExport:

    def test_export_calendar(self, tmp_path):
        _run_cli("create", "exp1", "-d", "Export test")
        _run_cli("add", "exp1", "--date", "2025-03-01")
        _run_cli("add", "exp1", "--date", "2025-06-15")
        out_file = tmp_path / "exported.cal"
        code, out = _run_cli("export", "exp1", "--output", str(out_file))
        assert code == 0
        assert "Exported" in out
        content = out_file.read_text()
        assert "2025-03-01" in content
        assert "2025-06-15" in content
        assert "# Calendar: exp1" in content
        assert "# Export test" in content

    def test_export_nonexistent_fails(self, tmp_path):
        code, out = _run_cli("export", "nope", "--output", str(tmp_path / "x.cal"))
        assert code == 1
        assert "not found" in out

    def test_export_import_round_trip(self, tmp_path):
        _run_cli("create", "rt_cal")
        _run_cli("add", "rt_cal", "--range", "2025-01-01", "2025-01-10")
        out_file = tmp_path / "rt_export.cal"
        _run_cli("export", "rt_cal", "--output", str(out_file))

        # Import into a new calendar with explicit name
        _run_cli("create", "rt_cal2")
        code, out = _run_cli("import", str(out_file), "--name", "rt_cal2")
        assert code == 0
        dates1 = _get_dates("rt_cal")
        dates2 = _get_dates("rt_cal2")
        assert dates1 == dates2


# ===========================================================================
# 8. list
# ===========================================================================

class TestList:

    def test_list_shows_date_count(self):
        _run_cli("create", "list1", "-d", "First")
        _run_cli("add", "list1", "--date", "2025-01-01")
        _run_cli("create", "list2", "-d", "Second")
        _run_cli("add", "list2", "--range", "2025-01-01", "2025-01-03")
        code, out = _run_cli("list")
        assert code == 0
        assert "list1" in out
        assert "list2" in out
        assert "1" in out  # list1 has 1 date
        assert "3" in out  # list2 has 3 dates

    def test_list_shows_empty_marker(self):
        # Create a calendar with no dates and verify it shows 0
        _run_cli("create", "empty_cal")
        code, out = _run_cli("list")
        assert code == 0
        assert "empty_cal" in out
        assert "0" in out


# ===========================================================================
# 9. Repository methods
# ===========================================================================

class TestRepository:

    def test_add_date(self):
        with sync_session() as session:
            row = CalendarRow(calendar_name="repo1", dates_json="[]")
            session.add(row)
            session.commit()

            assert _cal_repo.add_date(session, "repo1", "2025-05-01") is True
            dates = json.loads(_cal_repo.get(session, "repo1").dates_json)
            assert "2025-05-01" in dates

    def test_add_date_nonexistent(self):
        with sync_session() as session:
            assert _cal_repo.add_date(session, "nope", "2025-01-01") is False

    def test_add_dates_dedup(self):
        with sync_session() as session:
            row = CalendarRow(calendar_name="repo2", dates_json='["2025-01-01"]')
            session.add(row)
            session.commit()

            added = _cal_repo.add_dates(session, "repo2", ["2025-01-01", "2025-01-02", "2025-01-03"])
            assert added == 2  # 2025-01-01 already existed
            dates = json.loads(_cal_repo.get(session, "repo2").dates_json)
            assert len(dates) == 3
            assert dates == sorted(dates)  # sorted

    def test_remove_date(self):
        with sync_session() as session:
            row = CalendarRow(calendar_name="repo3", dates_json='["2025-01-01", "2025-02-01"]')
            session.add(row)
            session.commit()

            assert _cal_repo.remove_date(session, "repo3", "2025-01-01") is True
            dates = json.loads(_cal_repo.get(session, "repo3").dates_json)
            assert "2025-01-01" not in dates
            assert "2025-02-01" in dates

    def test_remove_date_not_found(self):
        with sync_session() as session:
            row = CalendarRow(calendar_name="repo4", dates_json='["2025-01-01"]')
            session.add(row)
            session.commit()

            assert _cal_repo.remove_date(session, "repo4", "2025-12-31") is False
