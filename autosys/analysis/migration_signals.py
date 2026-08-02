"""JIL structural analysis — extract migration signals from JIL attributes alone.

All functions in this module work purely from job definitions stored in the
database after JIL import. No runtime access required.

Each function returns a dict keyed by job_name with complexity signals that
are folded into the overall migration complexity score by complexity.py.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from autosys.db.schema import JobRow
from autosys.scheduler.state_machine import _norm_status


# ---------------------------------------------------------------------------
# A1. Machine concentration analysis
# ---------------------------------------------------------------------------

def machine_concentration(session: Session) -> dict:
    """
    Group CMD jobs by machine and compute concentration scores.

    Returns:
        {
            "machines": {machine_name: {"count": N, "jobs": [...]}},
            "by_job": {job_name: {"machine": str, "concentration": int, "score": str}},
        }
    """
    rows = list(session.scalars(select(JobRow)))
    by_machine: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if (r.job_type or "CMD").upper() == "CMD" and r.machine:
            by_machine[r.machine].append(r.job_name)

    machines = {}
    by_job = {}
    for machine, jobs in by_machine.items():
        count = len(jobs)
        if count > 40:
            score = "XL"
        elif count > 20:
            score = "L"
        elif count > 10:
            score = "M"
        elif count > 5:
            score = "S"
        else:
            score = "XS"
        machines[machine] = {"count": count, "jobs": jobs, "score": score}
        for j in jobs:
            by_job[j] = {"machine": machine, "concentration": count, "score": score}

    return {"machines": machines, "by_job": by_job}


# ---------------------------------------------------------------------------
# A2. Command analysis
# ---------------------------------------------------------------------------

_SCRIPT_RE = re.compile(r"(\S+\.\w+)")
_KSH_RE = re.compile(r"\.ksh\b")
_PERL_RE = re.compile(r"\.pl\b")
_PY_RE = re.compile(r"\.py\b")
_SH_RE = re.compile(r"\.sh\b")
_GLOBAL_RE = re.compile(r"%%(\w+)%%")


def command_analysis(session: Session) -> dict:
    """
    Parse command strings for migration signals.

    Returns:
        {job_name: {
            "scripts": [list of script paths],
            "dialect": "bash"|"python"|"ksh"|"perl"|None,
            "hardcoded_paths": [list],
            "business_globals": [list of %%VAR%% names],
            "score": "XS"|"S"|"M"|"L"|"XL",
        }}
    """
    rows = list(session.scalars(select(JobRow)))
    result = {}
    for r in rows:
        if not r.command:
            continue
        cmd = r.command
        scripts = _SCRIPT_RE.findall(cmd)
        dialect = None
        if _KSH_RE.search(cmd):
            dialect = "ksh"
        elif _PERL_RE.search(cmd):
            dialect = "perl"
        elif _PY_RE.search(cmd):
            dialect = "python"
        elif _SH_RE.search(cmd):
            dialect = "bash"

        hardcoded = [s for s in scripts if s.startswith("/")]
        globals_used = _GLOBAL_RE.findall(cmd)

        score = "XS"
        if dialect in ("ksh", "perl"):
            score = "L"
        elif dialect == "python":
            score = "S"
        elif dialect == "bash":
            score = "M"
        if len(hardcoded) > 3:
            score = "L" if score == "XS" else score

        result[r.job_name] = {
            "scripts": scripts,
            "dialect": dialect,
            "hardcoded_paths": hardcoded,
            "business_globals": globals_used,
            "score": score,
        }
    return result


# ---------------------------------------------------------------------------
# A3. Profile analysis
# ---------------------------------------------------------------------------

def profile_analysis(session: Session) -> dict:
    """
    Group jobs by profile attribute.

    Returns:
        {
            "profiles": {profile_path: {"count": N, "jobs": [...]}},
            "by_job": {job_name: {"profile": str, "shared": bool, "score": str}},
        }
    """
    rows = list(session.scalars(select(JobRow)))
    by_profile: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r.profile:
            by_profile[r.profile].append(r.job_name)

    profiles = {}
    by_job = {}
    for profile, jobs in by_profile.items():
        count = len(jobs)
        profiles[profile] = {"count": count, "jobs": jobs}
        for j in jobs:
            by_job[j] = {
                "profile": profile,
                "shared": count > 1,
                "score": "S" if count > 1 else "XS",
            }
    return {"profiles": profiles, "by_job": by_job}


# ---------------------------------------------------------------------------
# A4. Box nesting depth
# ---------------------------------------------------------------------------

def box_nesting_depth(session: Session) -> dict:
    """
    Recursively walk box_name references to compute nesting depth.

    Returns:
        {job_name: {"depth": int, "score": str}}
    """
    rows = list(session.scalars(select(JobRow)))
    by_name = {r.job_name: r for r in rows}

    def _depth(job_name: str, visited: set | None = None) -> int:
        if visited is None:
            visited = set()
        if job_name in visited:
            return 0  # cycle guard
        visited.add(job_name)
        row = by_name.get(job_name)
        if not row or not row.box_name:
            return 0
        return 1 + _depth(row.box_name, visited)

    result = {}
    for r in rows:
        d = _depth(r.job_name)
        if d >= 5:
            score = "XL"
        elif d >= 3:
            score = "L"
        elif d >= 2:
            score = "M"
        elif d >= 1:
            score = "S"
        else:
            score = "XS"
        result[r.job_name] = {"depth": d, "score": score}
    return result


# ---------------------------------------------------------------------------
# A5. Cross-box dependency detection
# ---------------------------------------------------------------------------

_COND_JOB_RE = re.compile(r"(?:success|failure|done|notrunning)\s*\(\s*([A-Za-z0-9_#.\-]+)\s*\)")


def cross_box_dependencies(session: Session) -> dict:
    """
    Find jobs whose condition references jobs in a different box.

    Returns:
        {
            "cross_box": [{job: str, depends_on: str, job_box: str, dep_box: str}],
            "by_job": {job_name: {"has_cross_box": bool, "count": int, "score": str}},
        }
    """
    rows = list(session.scalars(select(JobRow)))
    by_name = {r.job_name: r for r in rows}

    cross_box = []
    by_job = {}
    # Build a set of box names for quick lookup
    box_names = {r.job_name for r in rows if (r.job_type or "").upper() == "BOX"}
    for r in rows:
        if not r.condition:
            continue
        deps = _COND_JOB_RE.findall(r.condition)
        my_box = r.box_name or ""
        cross_deps = []
        for dep_name in deps:
            dep_row = by_name.get(dep_name)
            if dep_row is None:
                continue
            # If the dependency is a box, its "box" is itself
            if dep_name in box_names:
                dep_box = dep_name
            else:
                dep_box = dep_row.box_name or ""
            # Cross-box if the dependency's box differs from this job's box
            # and the dependency is not in the same box
            if dep_box and dep_box != my_box:
                cross_box.append({
                    "job": r.job_name,
                    "depends_on": dep_name,
                    "job_box": my_box,
                    "dep_box": dep_box,
                })
                cross_deps.append(dep_name)

        if cross_deps:
            by_job[r.job_name] = {
                "has_cross_box": True,
                "count": len(cross_deps),
                "score": "L",
            }

    return {"cross_box": cross_box, "by_job": by_job}


# ---------------------------------------------------------------------------
# A6. Schedule burst analysis
# ---------------------------------------------------------------------------

def schedule_burst_analysis(session: Session) -> dict:
    """
    Group jobs by start_times value to find schedule bursts.

    Returns:
        {
            "by_time": {"17:30": {"count": N, "jobs": [...]}, ...},
            "by_job": {job_name: {"start_time": str, "burst_count": int, "score": str}},
        }
    """
    rows = list(session.scalars(select(JobRow)))
    by_time: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r.start_times:
            # Handle both list and string (comma-separated) formats
            if isinstance(r.start_times, list):
                times = r.start_times
            else:
                times = str(r.start_times).split(",")
            for t in times:
                t_str = t.strip()
                if t_str:
                    by_time[t_str].append(r.job_name)

    by_job = {}
    for t, jobs in by_time.items():
        count = len(jobs)
        if count > 30:
            score = "L"
        elif count > 10:
            score = "M"
        elif count > 5:
            score = "S"
        else:
            score = "XS"
        by_time[t] = {"count": count, "jobs": jobs, "score": score}
        for j in jobs:
            by_job[j] = {
                "start_time": t,
                "burst_count": count,
                "score": score,
            }

    return {"by_time": dict(by_time), "by_job": by_job}


# ---------------------------------------------------------------------------
# A7. Notification mapping
# ---------------------------------------------------------------------------

def notification_mapping(session: Session) -> dict:
    """
    Tag jobs with notification_emailaddress or alarm_if_fail for callback migration.

    Returns:
        {job_name: {"emails": [str], "alarm_if_fail": bool, "alarm_if_terminated": bool, "score": str}}
    """
    rows = list(session.scalars(select(JobRow)))
    result = {}
    for r in rows:
        emails = []
        if r.notification_emailaddress:
            emails = [e.strip() for e in r.notification_emailaddress.split(",") if e.strip()]
        has_alarms = bool(r.alarm_if_fail or r.alarm_if_terminated)
        if emails and has_alarms:
            score = "M"
        elif emails or has_alarms:
            score = "S"
        else:
            continue  # skip jobs with no notification signals
        result[r.job_name] = {
            "emails": emails,
            "alarm_if_fail": bool(r.alarm_if_fail),
            "alarm_if_terminated": bool(r.alarm_if_terminated),
            "score": score,
        }
    return result


# ---------------------------------------------------------------------------
# A8. Log path analysis
# ---------------------------------------------------------------------------

def log_path_analysis(session: Session) -> dict:
    """
    Parse std_out_file/std_err_file for hardcoded paths.

    Returns:
        {job_name: {"stdout": str, "stderr": str, "hardcoded": bool, "score": str}}
    """
    rows = list(session.scalars(select(JobRow)))
    result = {}
    for r in rows:
        stdout = r.std_out_file or ""
        stderr = r.std_err_file or ""
        if not stdout and not stderr:
            continue
        hardcoded = bool(stdout.startswith("/") or stderr.startswith("/"))
        score = "M" if hardcoded else "XS"
        result[r.job_name] = {
            "stdout": stdout,
            "stderr": stderr,
            "hardcoded": hardcoded,
            "score": score,
        }
    return result


# ---------------------------------------------------------------------------
# A9. Owner/permission mapping
# ---------------------------------------------------------------------------

def owner_permission_mapping(session: Session) -> dict:
    """
    Group jobs by owner and parse permission string for RBAC mapping.

    Returns:
        {
            "by_owner": {owner: {"count": N, "jobs": [...]}},
            "by_job": {job_name: {"owner": str, "permissions": str, "score": str}},
        }
    """
    rows = list(session.scalars(select(JobRow)))
    by_owner: dict[str, list[str]] = defaultdict(list)
    by_job = {}
    for r in rows:
        owner = r.owner or "unknown"
        by_owner[owner].append(r.job_name)
        perm = r.permission or ""
        # permission format: gx,wx,me,mx,ge,we (6 tokens = complex)
        perm_tokens = [t.strip() for t in perm.split(",") if t.strip()]
        score = "M" if len(perm_tokens) >= 6 else ("S" if perm_tokens else "XS")
        by_job[r.job_name] = {
            "owner": owner,
            "permissions": perm,
            "perm_count": len(perm_tokens),
            "score": score,
        }
    return {"by_owner": dict(by_owner), "by_job": by_job}


# ---------------------------------------------------------------------------
# A10. Timezone analysis
# ---------------------------------------------------------------------------

def timezone_analysis(session: Session) -> dict:
    """
    Find jobs with timezone attribute for UTC conversion planning.

    Returns:
        {job_name: {"timezone": str, "score": str}}
    """
    rows = list(session.scalars(select(JobRow)))
    result = {}
    for r in rows:
        tz = r.timezone
        if not tz:
            continue
        result[r.job_name] = {
            "timezone": tz,
            "score": "S",
        }
    return result


# ---------------------------------------------------------------------------
# Convenience: run all analyses and return a combined dict
# ---------------------------------------------------------------------------

def run_all_structural_analyses(session: Session) -> dict:
    """Run all A1-A10 analyses and return combined result."""
    return {
        "machine_concentration": machine_concentration(session),
        "command_analysis": command_analysis(session),
        "profile_analysis": profile_analysis(session),
        "box_nesting_depth": box_nesting_depth(session),
        "cross_box_dependencies": cross_box_dependencies(session),
        "schedule_burst_analysis": schedule_burst_analysis(session),
        "notification_mapping": notification_mapping(session),
        "log_path_analysis": log_path_analysis(session),
        "owner_permission_mapping": owner_permission_mapping(session),
        "timezone_analysis": timezone_analysis(session),
    }
