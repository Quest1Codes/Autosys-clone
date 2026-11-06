"""
Disposition rules: what happens to each job on the way to Airflow (T16 in
the task13 assessment-report research's `07-build-tasks.md`).

Ported from the same research as `classification.py` (prompt 12, Part D),
read structurally -- condition shape, box membership, calendar definitions,
traits from classification -- not from raw command text a second time.

Four values, worst to best: `REDESIGN` (the mechanism itself goes away),
`JUDG` (needs a person or a customer answer), `MECH` (mechanical wrap),
`NONE` (no migration work). `UNCLASSIFIED` is kept separate: it is never a
disposition value, only a reason none could be assigned (P2 in
05-vishy-decisions.md: unknown is never trivial).

A job gets a (best, worst) pair, never averaged, matching the design's own
fix for the research's own measured problem: the same 4,427-job estate
scores 53.8% REDESIGN if vendor binaries disappear with AutoSys and 0.8%
if they stay callable, and picking one number hides that 53-point swing
from the reader.

**Honest gaps, not guesses.** Four rules in the research's own Part D table
need a distinction this version doesn't make, because it needs the raw
`job_type`, not just the class `classify_job` already collapsed several
job_types into (Part A's own table: HTTP and WSDOC both map to
`webservice_native`; SQL/DBPROC and DBMON/DBTRIG both map to
`database_native`). Rather than guess which job_type a row actually has
when the information needed to tell isn't threaded through yet, these four
default to the WORSE of their two possible answers (JUDG, never MECH) --
"absent over weak" (05-vishy-decisions.md's rule for the fact sheet, the
same principle applied here):

  - D-WS / D-WS-SOAP: `webservice_native` -- HTTP should be MECH-leaning,
    WSDOC/SOAP should stay JUDG. Both land on JUDG until job_type is
    threaded through.
  - D-NATIVE-DB / D-NATIVE-DB-TRIG: `database_native` -- SQL/DBPROC should
    be conditional on Q_VENDOR_RUNTIME like other native-vendor classes;
    DBMON/DBTRIG (polling semantics) should always be JUDG. Both land on
    JUDG.
  - D-FW / D-FW-REM: `file_watch`/`file_trigger` -- a remote watch path
    should be JUDG, a local one MECH. No remote-path detection exists yet
    (the watched path isn't in `command` the way an argument is), so both
    land on JUDG.
  - D-FT-NATIVE: `file_transfer_native` -- reachability depends on
    Q_NET, which this version accepts, but a specific remote endpoint
    check (does this job's own target even have a configured host?)
    isn't built, so "unsure" network answers also land on JUDG rather
    than printing a falsely confident MECH.

The research's own Part D table also lists `remote_exec` in both D-DIRECT's
MECH-able class set and, separately, D-REM as its own JUDG rule -- a real
contradiction in the ported design, not something I introduced. D-REM (the
more specific rule) wins here; D-DIRECT's class set excludes `remote_exec`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from autosys.analysis.classification import classify_row
from autosys.analysis.condition_refs import condition_job_refs
from autosys.db.schema import JobRow

Disposition = Literal["NONE", "MECH", "JUDG", "REDESIGN"]
_RANK: dict[Disposition, int] = {"NONE": 0, "MECH": 1, "JUDG": 2, "REDESIGN": 3}


def _worse(a: Disposition, b: Disposition) -> Disposition:
    return a if _RANK[a] >= _RANK[b] else b


@dataclass(frozen=True)
class TargetAnswers:
    """The not-yet-built questionnaire's answers (T26-T27), as explicit
    optional input rather than a live dependency on a system that doesn't
    exist in production yet -- this module is usable and testable now,
    and plugs into the real answers once they exist. Every field defaults
    to "unsure", matching the design's own rule: Unsure prints both
    scenarios, never a guess.

    `vendor_runtime` is keyed by class id, not job: the real questionnaire
    asks this once per integration family, not once per job (05
    §3d/§6.1 of the research)."""
    vendor_runtime: dict[str, Literal["callable", "retired", "unsure"]] = field(default_factory=dict)
    windows_host_plan: Literal["keep", "move", "unsure"] = "unsure"
    mainframe_bridge: Literal["bridge", "boundary_only", "unsure"] = "unsure"
    network_reachable: Literal["yes", "partial", "no", "unsure"] = "unsure"

    def vendor_runtime_for(self, class_id: str) -> Literal["callable", "retired", "unsure"]:
        return self.vendor_runtime.get(class_id, "unsure")


@dataclass(frozen=True)
class JobDisposition:
    job_name: str
    class_: str
    best: Disposition
    worst: Disposition
    rule_id: str
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Windows as a host trait (Q5's fix), not a class membership test: a job
# can be erp_native_sap AND windows-shaped at the same time, and both
# answers apply with the stricter one winning.
# ---------------------------------------------------------------------------

# Ported verbatim from dev/task13-assessment-report-research/evidence/
# verify_prompt08.py:88-90, the script behind the verified 999-job figure
# (05-vishy-decisions.md's correction of an earlier, narrower 926 count).
# The third alternative in the path-shape check, `e\:`, is redundant with
# `[A-Za-z]:` (which already matches a literal "e:") -- kept exactly as
# validated rather than "cleaned up" and risking a different count than
# the one that's actually been cited.
_WIN_EXT_RE = re.compile(r"\.(bat|cmd|exe)$", re.IGNORECASE)
_WIN_PATH_RE = re.compile(r'"?([A-Za-z]:|\\\\|e\\:)')


def is_windows_shaped(command: str | None) -> bool:
    """A job is Windows-shaped if its executable ends .bat/.cmd/.exe, or
    its raw command opens with a drive letter or a UNC path."""
    if not command:
        return False
    from autosys.analysis.classification import executable
    return bool(_WIN_EXT_RE.search(executable(command)) or _WIN_PATH_RE.match(command))


# ---------------------------------------------------------------------------
# Fixed-disposition classes (D-NOOP, D-SCHED, D-LEGACY, D-REM, D-USER, and
# the honest-gap classes above, each pinned to the worse of their two
# possible answers).
# ---------------------------------------------------------------------------

_FIXED: dict[str, tuple[Disposition, Disposition, str]] = {
    "no_op":                ("NONE", "NONE", "D-NOOP"),
    "scheduler_self_call":  ("REDESIGN", "REDESIGN", "D-SCHED"),
    "cross_scheduler":      ("REDESIGN", "REDESIGN", "D-SCHED"),
    "hadoop_legacy":        ("JUDG", "JUDG", "D-LEGACY"),
    "remote_exec":          ("JUDG", "JUDG", "D-REM"),
    "user_defined_type":    ("JUDG", "JUDG", "D-USER"),
    "mainframe_native":     ("JUDG", "JUDG", "D-MF"),
    # Honest gaps (module docstring): both land on JUDG, not a guessed MECH.
    "webservice_native":    ("JUDG", "JUDG", "D-WS"),
    "database_native":      ("JUDG", "JUDG", "D-NATIVE-DB"),
    "file_watch":           ("JUDG", "JUDG", "D-FW"),
    "file_trigger":         ("JUDG", "JUDG", "D-FW"),
}

# D-DIRECT: MECH-able as-is, with no vendor-runtime question attached.
# remote_exec deliberately excluded -- see the module docstring's note on
# the contradiction in the ported research between D-DIRECT and D-REM.
_DIRECT_MECH_CLASSES = frozenset({
    "unix_script", "windows_script", "windows_exe", "perl", "python", "java", "crypto", "fs_ops",
    "db_oracle", "db_teradata", "db_db2", "db_mssql", "db_postgres", "db_mysql", "db_snowflake",
    "db_netezza", "db_vertica", "db_informix", "db_ambiguous", "transfer_ftp", "transfer_rsync",
    "notify", "hadoop_hdfs", "hadoop_spark", "hadoop_pig", "hadoop_flink", "hadoop_kafka",
    "cloud_aws", "cloud_gcp", "cloud_azure", "cloud_k8s",
})

# D-VENDOR / D-NATIVE-VENDOR: disposition is a function of Q_VENDOR_RUNTIME.
_VENDOR_CONDITIONAL_CLASSES = frozenset({
    "erp_native_sap", "erp_native_peoplesoft", "erp_native_oracle_ebs", "informatica_native",
    "j2ee_native", "process_automation_native",
    "erp_sap", "erp_peoplesoft", "erp_oracle_ebs", "etl_informatica", "etl_datastage",
    "etl_abinitio", "etl_talend", "etl_ssis", "etl_odi", "transfer_mft", "hadoop_native", "hadoop_hive",
})


def _class_base(class_id: str, answers: TargetAnswers) -> tuple[Disposition, Disposition, str]:
    if class_id in ("box", "unclassified", "no_command"):
        raise ValueError(f"{class_id} has its own caller-side handling, not a class base")
    if class_id in _FIXED:
        return _FIXED[class_id]
    if class_id in _VENDOR_CONDITIONAL_CLASSES:
        verdict = answers.vendor_runtime_for(class_id)
        rule_id = "D-NATIVE-VENDOR" if class_id.endswith("_native") or class_id in (
            "erp_native_sap", "erp_native_peoplesoft", "erp_native_oracle_ebs", "informatica_native",
            "j2ee_native", "process_automation_native") else "D-VENDOR"
        if verdict == "callable":
            return ("MECH", "MECH", rule_id)
        if verdict == "retired":
            return ("REDESIGN", "REDESIGN", rule_id)
        return ("MECH", "REDESIGN", rule_id)  # unsure: print both scenarios
    if class_id in _DIRECT_MECH_CLASSES:
        return ("MECH", "MECH", "D-DIRECT")
    # A class this table has no opinion on yet is a gap in this port, not
    # a silent MECH guess.
    return ("JUDG", "JUDG", "D-UNMAPPED-CLASS")


def build_manifest(rows: list[JobRow], session, answers: TargetAnswers | None = None) -> list[JobDisposition]:
    """Every stored job -> one JobDisposition. `session` is used once, to
    reuse `migration_signals.cross_box_dependencies` rather than
    re-deriving cross-box detection a second way (one definition per
    concept, same reasoning as `condition_job_refs`'s own docstring)."""
    from autosys.analysis.migration_signals import cross_box_dependencies
    from autosys.db.repository import calendars as calendar_repo

    answers = answers or TargetAnswers()
    all_names = {r.job_name for r in rows}
    box_children: dict[str, int] = {}
    for r in rows:
        if r.box_name:
            box_children[r.box_name] = box_children.get(r.box_name, 0) + 1
    defined_calendars = {c.calendar_name for c in calendar_repo.list_all(session)}
    cross_box = cross_box_dependencies(session)["by_job"]

    out = []
    for r in rows:
        jt = (r.job_type or "").upper()
        if jt == "BOX":
            n = box_children.get(r.job_name, 0)
            best = worst = "NONE" if n == 0 else "MECH"
            out.append(JobDisposition(r.job_name, "box", best, worst, "D-BOX-EMPTY" if n == 0 else "D-BOX"))
            continue

        cls_result = classify_row(r)
        class_id = cls_result["class"]
        notes: list[str] = []

        if class_id == "unclassified":
            out.append(JobDisposition(r.job_name, class_id, "JUDG", "JUDG", "D-UNCLASS",
                                       ("not a count of mechanical work; a person has to look",)))
            continue

        best, worst, rule_id = _class_base(class_id, answers)

        # Mainframe bridge answer can soften D-MF's default JUDG/JUDG only
        # when explicitly resolved -- unsure stays JUDG both sides.
        if class_id == "mainframe_native" and answers.mainframe_bridge == "boundary_only":
            best, worst, rule_id = "MECH", "MECH", "D-MF"

        # D-ENV (the D11 tightening): any AutoSys/global/Windows env-var
        # trait on an otherwise-MECH-able job drops it to JUDG -- the
        # wrapper itself is fine, its inputs may not exist post-AutoSys.
        if {"T-ENV-AUTOSYS", "T-GLOBAL-VAR", "T-ENV-WIN"} & set(cls_result["traits"]):
            best = _worse(best, "JUDG")
            worst = _worse(worst, "JUDG")
            notes.append("env-var coupling (D-ENV)")

        # D-NONRUN: a notrunning()/n() condition is a scheduler-runtime
        # semantic Airflow lacks, regardless of class.
        refs = condition_job_refs(r.condition)
        if r.condition and re.search(r"(?<![A-Za-z0-9_])(?:n|notrunning)\s*\(", r.condition, re.IGNORECASE):
            best = worst = "REDESIGN"
            rule_id = "D-NONRUN"

        # D-COND-OR: an OR in the condition.
        if r.condition and re.search(r"\||\bOR\b", r.condition, re.IGNORECASE):
            best = _worse(best, "JUDG")
            worst = _worse(worst, "JUDG")
            notes.append("condition has an OR (D-COND-OR)")

        # D-XBOX: condition crosses a box boundary.
        if cross_box.get(r.job_name, {}).get("has_cross_box"):
            best = _worse(best, "JUDG")
            worst = _worse(worst, "JUDG")
            notes.append("condition crosses a box boundary (D-XBOX)")

        # D-DANGLE: condition references a job not in this file.
        if refs - all_names:
            best = _worse(best, "JUDG")
            worst = _worse(worst, "JUDG")
            notes.append(f"{len(refs - all_names)} dangling reference(s) (D-DANGLE)")

        # D-CAL: a named calendar that isn't defined in this import.
        named_cals = {c for c in (r.run_calendar, r.exclude_calendar) if c}
        if named_cals and defined_calendars and (named_cals - defined_calendars):
            best = _worse(best, "JUDG")
            worst = _worse(worst, "JUDG")
            notes.append("calendar referenced but not defined (D-CAL)")

        # Windows as a host trait (Q5): applies on top of whatever the
        # class gave, stricter side wins. A Windows job already at JUDG
        # or REDESIGN doesn't move; one that was MECH does, because the
        # host decision (keep Windows, or move to Linux/containers) is
        # still open unless the questionnaire already answered it.
        if is_windows_shaped(r.command):
            if answers.windows_host_plan == "unsure":
                best = _worse(best, "JUDG")
                worst = _worse(worst, "JUDG")
                notes.append("Windows-shaped, host plan open (D-WIN)")
            elif answers.windows_host_plan == "move":
                worst = _worse(worst, "JUDG")  # re-platforming the script itself needs a look
                notes.append("Windows-shaped, moving off Windows hosts (D-WIN)")
            # "keep": retained as a remote Windows host, no extra JUDG.

        out.append(JobDisposition(r.job_name, class_id, best, worst, rule_id, tuple(notes)))
    return out
