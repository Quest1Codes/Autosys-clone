"""
Clean-up candidates: which jobs look like test, dummy or retired work left
behind in the export (T19's "anchored clean-up rule", task13
assessment-report research prompt 14, §5 of `14-llm-insights-opus5.5.md`).

Ported as the rule it replaces, not the LLM it was tested against. The
research's first keyword rule (`old|bak|backup|temp|tmp|test|tst|copy|
obsolete|retired|disabled|dummy|...`, matched anywhere in a job name or
description) fired on 50 of 4,427 jobs but included 29 false matches on
"copy" and similar unanchored substrings -- a job that copies a file isn't
a test job. The narrower `ANCHOR` pattern below, matched only against
whole name segments (delimited by `_` or the start/end of the name), is
what a model was tested against afterward and agreed with on 40 of 40
candidates: that test is what's ported here. A model review is no longer
needed for this signal (prompt 14 §6.3: "Drop. The rule matched it, 40 of
40").

Two independent signals, kept separate because they catch different
things: `is_test_or_dummy_named` reads the job *name*; `has_retirement_language`
reads free-text *descriptions*, which fewer estates will even have (05 §3h:
real free text was on only 1,469 of 4,427 rows on the one real sample
checked).
"""
from __future__ import annotations

import re

# Anchored to a whole name segment (underscore-delimited, or the start/end
# of the name) so "copy_report.sh" or "backup_loader" don't false-match on
# a job that merely contains "copy"/"bak" as a substring somewhere else in
# a longer word.
_TEST_OR_DUMMY = re.compile(r"(?i)(^|_)(test|tst|dummy)(_|$)")

# Explicit retirement language in free text -- a different, much rarer
# signal than the name pattern above.
_RETIREMENT_LANGUAGE = re.compile(
    r"(?i)\b(do not run|obsolete|retired|no longer|decom\w*|not used|unused|deprecated|on hold)\b"
)


def is_test_or_dummy_named(job_name: str) -> bool:
    """True when `job_name` has "test", "tst" or "dummy" as a whole
    underscore-delimited segment, or as the first/last segment. Anchored
    on purpose, checked against both directions: "test_load" and
    "load_test" both match (word is its own segment); "autotest_runner"
    and "testament_job" do NOT ("test" has no "_" or start/end boundary
    right next to it, so it's just part of a longer word, same as
    "copy_report" or "backup_loader" never matching "test"/"dummy" at
    all)."""
    return bool(_TEST_OR_DUMMY.search(job_name or ""))


def has_retirement_language(description: str | None) -> bool:
    """True when a job's description explicitly says it's retired,
    deprecated, unused or should not run -- never guessed from the name."""
    return bool(_RETIREMENT_LANGUAGE.search(description or ""))
