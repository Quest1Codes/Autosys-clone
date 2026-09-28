from datetime import datetime, timezone

from autosys.timeutil import utcnow


def test_utcnow_is_naive_and_utc():
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    now = utcnow()
    after = datetime.now(timezone.utc).replace(tzinfo=None)
    assert now.tzinfo is None
    assert before <= now <= after
