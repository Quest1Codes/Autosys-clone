import os
import pytest
from datetime import datetime, date

os.environ.setdefault("AUTOSYS_DB_URL", "sqlite:///file::memory:?cache=shared&uri=true")

from autosys.db.schema import JobRow, CalendarRow
from autosys.models.calendar import Calendar
from autosys.db.connection import sync_session
from autosys.db.repository import calendars as calendar_repo
from autosys.scheduler.time_trigger import is_triggered

@pytest.fixture(scope="session", autouse=True)
def fresh_db():
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=True)

def test_calendar_repo_crud():
    with sync_session() as session:
        # Create
        cal_row = CalendarRow(
            calendar_name="holidays",
            dates_json='["2026-12-25", "2026-01-01"]',
            description="Public holidays"
        )
        calendar_repo.upsert(session, cal_row)
        
        # Read
        fetched = calendar_repo.get(session, "holidays")
        assert fetched is not None
        assert fetched.description == "Public holidays"
        
        # List all
        all_cals = calendar_repo.list_all(session)
        names = [c.calendar_name for c in all_cals]
        assert "holidays" in names
        
        # Delete
        assert calendar_repo.delete(session, "holidays") is True
        assert calendar_repo.get(session, "holidays") is None

def test_is_triggered_with_run_calendar():
    # Job that should only run on specific calendar dates
    row = JobRow(
        job_name="cal_job",
        status=8,
        start_times='"06:00"',
        run_calendar="month_end"
    )
    
    cal_dates = "2026-01-31, 2026-02-28, 2026-03-31"
    calendars = {
        "month_end": Calendar(calendar_name="month_end", dates=cal_dates)
    }
    
    # Not a month end
    now_wrong_day = datetime(2026, 1, 15, 6, 0)
    assert not is_triggered(row, now_wrong_day, calendars)
    
    # Is month end, wrong time
    now_wrong_time = datetime(2026, 1, 31, 7, 0)
    assert not is_triggered(row, now_wrong_time, calendars)
    
    # Is month end, right time
    now_right = datetime(2026, 1, 31, 6, 0)
    assert is_triggered(row, now_right, calendars)
    
    # Missing calendar evaluates to False
    assert not is_triggered(row, now_right, calendars={})

def test_is_triggered_with_exclude_calendar():
    # Job that runs every day at 06:00 EXCEPT holidays
    row = JobRow(
        job_name="daily_job",
        status=8,
        start_times='"06:00"',
        exclude_calendar="holidays"
    )
    
    calendars = {
        "holidays": Calendar(calendar_name="holidays", dates="2026-12-25, 2026-01-01")
    }
    
    # Normal day
    now_normal = datetime(2026, 1, 15, 6, 0)
    assert is_triggered(row, now_normal, calendars)
    
    # Holiday
    now_holiday = datetime(2026, 12, 25, 6, 0)
    assert not is_triggered(row, now_holiday, calendars)
