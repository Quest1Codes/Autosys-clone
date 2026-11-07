"""
T26: the pre-scan -- a light, read-only summary of a stored estate, built
to replace the connectivity check's current habit of running the whole
heavy `/report` pipeline (scoring, simulation, structural analysis) just
to keep `job_count` (N5 in `llm-responses/11-questionnaire-redesign-
opus5.5.md`). No scoring, no simulation: this module only counts and
classifies.

Everything here is a pure function of already-stored `JobRow`s -- no
session, no catalog table lookups (machines/calendars/globals *defined*,
run history). The router (`app_server/routers/assessment.py`) merges this
with the handful of fields that genuinely need a session, matching the
field table in `llm-responses/11-questionnaire-redesign-opus5.5.md` §3.3.

Classification lives with the simulator (R7, same reasoning as T15), so
pre-scan's classes and the real manifest's classes can never disagree.
"""
from __future__ import annotations

import collections
import hashlib
import json
from typing import Any

from autosys.analysis.classification import classify_row
from autosys.analysis.condition_refs import condition_job_refs
from autosys.analysis.disposition import is_windows_shaped
from autosys.analysis.size_model import FAMILY, OTHER_INTEGRATION, UNKNOWN_CLASSES, _GLOBAL_RE, split_machines
from autosys.db.schema import JobRow

# Every marker classify_row's `traits` can carry that is evidence of a
# credential *shape* -- the free-form flags redact_and_flag sets, plus the
# two rule-defined trait ids (see classification_rules.json's `traits`).
# The other trait ids (T-ENV-AUTOSYS, T-GLOBAL-VAR, T-WIN-PATH, ...) are
# real signals but not credential ones, and must not be counted here.
_CRED_MARKERS = frozenset({
    "inline_kv_secret", "inline_userpass_at", "inline_flag_secret", "sshpass_inline",
    "inline_userpass_flag", "inline_db_connect", "credential_via_scriptfile",
    "positional_credential_shape", "T-CRED-INLINE", "T-CRED-DB",
})


def _fingerprint(rows: list[JobRow]) -> str:
    """Same estate, same fingerprint, regardless of row order -- used by
    the caller (T26's §3.4) to detect whether the JIL changed between the
    pre-scan and a later run, not computed fresh each time against clock
    time or row id."""
    body = sorted((r.job_name, r.job_type) for r in rows)
    return hashlib.sha256(json.dumps(body).encode()).hexdigest()[:16]


def compute_prescan(rows: list[JobRow]) -> dict[str, Any]:
    """Every field in the design's pre-scan response that's derivable from
    `rows` alone. `machines_defined`, `calendars_defined`, `globals_defined`
    and `history_jobs` are deliberately absent -- they need the machine,
    calendar, global-variable and run-history tables, which only the
    router can reach (it has the session)."""
    names = {r.job_name for r in rows}
    by_job_type: collections.Counter = collections.Counter()
    machines_named: set[str] = set()
    calendars_named: set[str] = set()
    globals_named: set[str] = set()
    clock_jobs = 0
    clock_no_tz = 0
    dangling_refs: set[str] = set()
    credential_jobs = 0
    windows_jobs = 0
    mainframe_native = 0
    unclassified = 0
    jobs_with_app_or_group = 0
    families: dict[str, dict[str, int]] = collections.defaultdict(lambda: {"wrapper": 0, "native": 0})

    for r in rows:
        jt = (r.job_type or "CMD").upper()
        by_job_type[jt] += 1
        for m in split_machines(r.machine):
            machines_named.add(m)
        for cal in (r.run_calendar, r.exclude_calendar):
            if cal and cal.strip():
                calendars_named.add(cal.strip())
        for text in (r.command, r.std_out_file, r.std_err_file, r.std_in_file):
            if text:
                globals_named.update(_GLOBAL_RE.findall(text))
        if r.start_times or r.start_mins:
            clock_jobs += 1
            if not r.timezone:
                clock_no_tz += 1
        for ref in condition_job_refs(r.condition):
            if ref not in names:
                dangling_refs.add(ref)
        if r.application or r.sub_application or r.group:
            jobs_with_app_or_group += 1

        if jt == "BOX":
            continue  # boxes carry no command, no class, no family membership

        result = classify_row(r)
        cls = result["class"]
        if cls == "unclassified":
            unclassified += 1
        if cls == "mainframe_native":
            mainframe_native += 1
        if _CRED_MARKERS.intersection(result["traits"]):
            credential_jobs += 1
        if is_windows_shaped(r.command):
            windows_jobs += 1
        family, kind = FAMILY.get(cls, (OTHER_INTEGRATION.get(cls), None))
        if family and kind:
            families[family][kind] += 1

    return {
        "job_count": len(rows),
        "non_box": sum(1 for r in rows if (r.job_type or "CMD").upper() != "BOX"),
        "by_job_type": dict(by_job_type),
        "machines_named": len(machines_named),
        "calendars_named": len(calendars_named),
        "globals_named": len(globals_named),
        "clock_jobs": clock_jobs,
        "clock_no_tz": clock_no_tz,
        "dangling_refs": len(dangling_refs),
        "credential_jobs": credential_jobs,
        "windows_jobs": windows_jobs,
        "mainframe_native": mainframe_native,
        "families": {k: dict(v) for k, v in families.items()},
        "unclassified": unclassified,
        "jobs_with_app_or_group": jobs_with_app_or_group,
        "fingerprint": _fingerprint(rows),
    }
