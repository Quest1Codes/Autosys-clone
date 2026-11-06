"""T19: the anchored clean-up rule that replaced an LLM pass (U12)."""
from __future__ import annotations

import pytest

from autosys.analysis.cleanup_candidates import has_retirement_language, is_test_or_dummy_named


@pytest.mark.parametrize("name", [
    "test_load", "load_test", "nightly_test", "daily_test_run", "tst_feed",
    "dummy_job", "job_dummy", "TEST_UPPER", "test",
])
def test_catches_test_and_dummy_names(name):
    assert is_test_or_dummy_named(name)


@pytest.mark.parametrize("name", [
    # The exact false-match class the research found with the unanchored
    # keyword rule (29 of 50 hits on "copy" and similar).
    "copy_report", "backup_loader", "autotest_runner", "testament_job",
    "contest_handler", "attestation_job", "protest_march", "latest_feed",
])
def test_does_not_false_match_substrings(name):
    assert not is_test_or_dummy_named(name)


@pytest.mark.parametrize("desc", [
    "Deprecated, do not run",
    "Obsolete since 2024 migration",
    "Retired -- superseded by new_loader",
    "No longer needed",
    "Decommissioned job, kept for audit",
    "Not used in production",
])
def test_catches_retirement_language(desc):
    assert has_retirement_language(desc)


@pytest.mark.parametrize("desc", [
    None, "", "Loads the daily sales file", "Runs the nightly reconciliation",
])
def test_does_not_false_match_ordinary_descriptions(desc):
    assert not has_retirement_language(desc)
