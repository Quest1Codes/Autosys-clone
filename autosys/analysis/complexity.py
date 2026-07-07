"""
Job/Box complexity scoring — Phase 1 migration assessment.

Applies the T-shirt sizing model from docs/strategy-and-approach.md to score
every job definition and estimate migration effort.  Consumed by both the
`autosys analyze` CLI and the `/api/v1/assessment/report` REST endpoint so
the two never drift apart.

Complexity sizes (from docs/strategy-and-approach.md)
------------------------------------------------------
  XS  Simple command job, no dependencies.                      1-2 h per DAG
  S   Box with linear deps OR cmd with one simple condition.    2-4 h per DAG
  M   Calendar, time triggers, date_conditions, or file-watchers. 4-8 h per DAG
  L   look_back, virtual resources, complex conditions.         1-3 days per DAG
  XL  FTP jobs, cross-instance deps, deep BOX trees.             1+ week per DAG
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from autosys.analysis.dependency_graph import dependency_wave, fan_in_counts
from autosys.analysis.gap_analysis import GAP_CATALOGUE, compute_gap_tags
from autosys.analysis.operational_risk import RISK_LEVELS, RunStats, score_operational_risk
from autosys.db.schema import JobRow

# ---------------------------------------------------------------------------
# T-shirt size catalogue
# ---------------------------------------------------------------------------

SIZES = ("XS", "S", "M", "L", "XL")

# Effort in hours — mid-point of each range in the strategy doc
EFFORT_HOURS: dict[str, int] = {
    "XS": 2,    # 1-2 h
    "S":  4,    # 2-4 h
    "M":  8,    # 4-8 h
    "L":  24,   # 1-3 days  (1 day = 8 h)
    "XL": 60,   # 1+ week   (1.5 weeks x 5 days x 8 h)
}

SIZE_DESCRIPTIONS: dict[str, str] = {
    "XS": "Simple CMD, no deps",
    "S":  "Linear deps / simple BOX",
    "M":  "Calendar / time / file triggers",
    "L":  "look_back / resources / complex conditions",
    "XL": "FTP / cross-instance / re-engineering",
}

# Overhead percentages applied on top of raw job effort (strategy doc)
PLATFORM_OVERHEAD_PCT = 0.20
TESTING_OVERHEAD_PCT  = 0.30
PM_OVERHEAD_PCT       = 0.15
TRAINING_OVERHEAD_PCT = 0.10

# Standard AutoSys date/time built-ins — not counted as "business" global vars
_DT_BUILTINS: frozenset[str] = frozenset({
    "DATE", "YEAR", "MM", "DD", "TIME", "HH", "MIN",
    "ODATE", "OYEAR", "OMM", "ODD", "OTIME",
    "TIMESTAMP", "UNIX_TIMESTAMP",
})

# Dependency-chain depth thresholds (see dependency_graph.dependency_wave).
# A chain this deep can't be trivially parallelised into independent Airflow
# tasks, regardless of how many boolean operators its conditions use.
WAVE_DEPTH_L_THRESHOLD  = 4
WAVE_DEPTH_XL_THRESHOLD = 7


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class JobAssessment:
    job_name: str
    job_type: str
    box_name: str
    size:     str
    effort_h: int
    drivers:  str
    # Operational risk (FSM current state + JobRunRow/AlarmRow history) —
    # orthogonal to size/effort_h, see operational_risk.py. Defaults to
    # NO_DATA so existing positional-construction call sites (e.g. tests)
    # keep working when a caller doesn't supply run_stats.
    risk:         str = "NO_DATA"
    risk_drivers: str = ""
    # Cross-job blast radius — how many other jobs' conditions reference
    # this one (see dependency_graph.fan_in_counts).
    blast_radius: int = 0
    # Airflow gap tags — see gap_analysis.GAP_CATALOGUE.
    gap_tags:     str = ""


@dataclass
class AssessmentSummary:
    counts:      dict[str, int]
    hours:       dict[str, int]
    total_jobs:  int
    raw_hours:   int
    platform_h:  int
    testing_h:   int
    pm_h:        int
    training_h:  int
    total_h:     int
    total_days:  int
    risk_counts:          dict[str, int] = field(default_factory=dict)
    gap_severity_counts:  dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Complexity scorer
# ---------------------------------------------------------------------------

def _count_condition_operators(condition: Optional[str]) -> int:
    """Return the total count of & and | operators in a condition string."""
    if not condition:
        return 0
    return condition.count("&") + condition.count("|")


def _has_look_back(condition: Optional[str]) -> bool:
    """Return True if condition contains a look_back arg: success(job,12)."""
    if not condition:
        return False
    return bool(re.search(r"\(\s*\w[\w_]+\s*,\s*\d+", condition))


def _business_globals(command: Optional[str]) -> list[str]:
    """Return %%VAR%% tokens in command that are not AutoSys date/time builtins."""
    if not command:
        return []
    all_vars = re.findall(r"%%([A-Z_][A-Z_0-9]*)%%", command)
    return [v for v in all_vars if v not in _DT_BUILTINS]


def score_job(
    row: JobRow,
    all_rows: dict[str, "JobRow"],
    wave_depth: int = 1,
) -> tuple[str, list[str]]:
    """
    Assign a T-shirt size to *row* and return (size, [driver_strings]).

    Priority: XL -> L -> M -> S -> XS.

    *wave_depth* is the job's dependency-chain depth within its box (see
    dependency_graph.dependency_wave), precomputed once per box by
    build_report — a box where success(a)->success(b)->success(c) chains
    deep can't be trivially parallelised into independent Airflow tasks,
    which the &/| operator count below can't see on its own.
    """
    xl: list[str] = []
    l:  list[str] = []
    m:  list[str] = []
    s:  list[str] = []

    jt = (row.job_type or "CMD").upper()

    if wave_depth >= WAVE_DEPTH_XL_THRESHOLD:
        xl.append(f"dependency chain depth={wave_depth}")
    elif wave_depth >= WAVE_DEPTH_L_THRESHOLD:
        l.append(f"dependency chain depth={wave_depth}")

    # ---- XL signals -------------------------------------------------------
    if jt == "FTP":
        xl.append(f"FTP job ({row.ftp_type or 'unknown'})")

    if row.condition and "^" in row.condition:
        xl.append("cross-instance dependency (^)")

    if jt == "BOX":
        child_count = sum(1 for r in all_rows.values() if r.box_name == row.job_name)
        if child_count > 15:
            xl.append(f"BOX with {child_count} children (>15)")

    # ---- L signals --------------------------------------------------------
    if _has_look_back(row.condition):
        l.append("look_back condition")

    op_count = _count_condition_operators(row.condition)
    if op_count >= 3:
        l.append(f"complex condition ({op_count} operators)")

    biz_vars = _business_globals(row.command)
    if len(biz_vars) >= 3:
        l.append(f"{len(biz_vars)} business globals: {', '.join(biz_vars[:3])}...")

    if row.resources:
        l.append(f"virtual resource: {row.resources}")

    if row.box_success or row.box_failure:
        l.append("custom box_success/box_failure")

    if row.max_exit_success is not None:
        l.append(f"max_exit_success={row.max_exit_success}")

    if (row.n_retrys or 0) >= 3:
        l.append(f"n_retrys={row.n_retrys}")

    # ---- M signals --------------------------------------------------------
    if row.run_calendar:
        m.append(f"run_calendar={row.run_calendar}")
    if row.exclude_calendar:
        m.append(f"exclude_calendar={row.exclude_calendar}")
    if row.date_conditions:
        m.append("date_conditions=1")
    if jt == "FILEWATCH":
        m.append(f"FILEWATCH ({row.watch_file or '?'})")
    if row.start_times:
        m.append(f"start_times={row.start_times}")
    if row.start_mins:
        m.append("start_mins set")
    if row.run_window:
        m.append(f"run_window={row.run_window}")
    if row.must_start_times:
        m.append("must_start_times")
    if row.must_complete_times:
        m.append("must_complete_times")
    if row.timezone:
        m.append(f"timezone={row.timezone}")
    if row.term_run_time:
        m.append(f"term_run_time={row.term_run_time}m")
    if (row.n_retrys or 0) in (1, 2):
        m.append(f"n_retrys={row.n_retrys}")
    if 1 <= op_count <= 2:
        m.append(f"compound condition ({op_count} operators)")
    if 1 <= len(biz_vars) < 3:
        m.append(f"business globals: {', '.join(biz_vars)}")

    # ---- S signals --------------------------------------------------------
    if jt == "BOX":
        child_count = sum(1 for r in all_rows.values() if r.box_name == row.job_name)
        s.append(f"BOX ({child_count} children)")
    if row.condition and not l and not m:
        s.append(f"condition: {row.condition[:70]}")
    if row.days_of_week:
        s.append(f"days_of_week={row.days_of_week}")
    if row.alarm_if_fail:
        s.append("alarm_if_fail")

    # ---- Apply priority -----------------------------------------------------
    if xl:
        return "XL", xl
    if l:
        return "L", l
    if m:
        return "M", m
    if s or row.condition:
        return "S", s or [f"condition: {(row.condition or '')[:70]}"]
    return "XS", ["no dependencies or schedule complexity"]


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------

def build_report(
    rows: list[JobRow],
    box_pattern: Optional[str] = None,
    run_stats: Optional[dict[str, RunStats]] = None,
) -> list[JobAssessment]:
    """
    Score every job and return a list of JobAssessment results.

    When *box_pattern* is given (SQL LIKE syntax with %-wildcards), restrict
    to BOX jobs matching the pattern and their children.

    *run_stats* is an optional {job_name: RunStats} map (see
    operational_risk.fetch_run_stats) used to score operational risk. This
    function itself stays DB-session-free — callers that want risk scoring
    fetch run_stats themselves and pass it in; omitting it scores every job
    as NO_DATA risk.
    """
    all_by_name: dict[str, JobRow] = {r.job_name: r for r in rows}
    # Blast radius is a global-graph property — computed once over the full,
    # unfiltered job set before any box_pattern narrowing below.
    blast_radius = fan_in_counts(all_by_name)
    results: list[JobAssessment] = []

    if box_pattern:
        pat = box_pattern.replace("%", ".*").lower()
        box_names = {
            r.job_name
            for r in rows
            if r.job_type and r.job_type.upper() == "BOX"
            and re.fullmatch(pat, r.job_name.lower())
        }
        rows = [
            r for r in rows
            if r.job_name in box_names
            or r.box_name in box_names
        ]

    # Sort: BOX jobs first (alphabetical), then children under their parent
    boxes   = [r for r in rows if not r.box_name]
    orphans = []  # children whose parent box wasn't included in the filter
    child_map: dict[str, list[JobRow]] = {}
    for r in rows:
        if r.box_name:
            child_map.setdefault(r.box_name, []).append(r)

    ordered: list[JobRow] = []
    for b in sorted(boxes, key=lambda r: r.job_name):
        ordered.append(b)
        for c in sorted(child_map.get(b.job_name, []), key=lambda r: r.job_name):
            ordered.append(c)
    seen = {r.job_name for r in ordered}
    for r in rows:
        if r.job_name not in seen:
            orphans.append(r)
    ordered.extend(sorted(orphans, key=lambda r: r.job_name))

    # Dependency-chain depth (see dependency_graph.dependency_wave), computed
    # once per scope group rather than per-row: the root scope (top-level
    # jobs referencing each other) and one scope per BOX (its own children).
    # Orphans (children whose parent box fell outside a box_pattern filter)
    # get a conservative depth of 1 — there isn't enough context to walk
    # their chain.
    wave_depth_by_name: dict[str, int] = {}
    root_scope = {b.job_name for b in boxes}
    root_conditions = {name: all_by_name[name].condition for name in root_scope}
    root_memo: dict[str, int] = {}
    for b in boxes:
        wave_depth_by_name[b.job_name] = dependency_wave(
            b.job_name, root_conditions, root_scope, root_memo
        )
        children = child_map.get(b.job_name, [])
        if not children:
            continue
        child_scope = {b.job_name} | {c.job_name for c in children}
        child_conditions = {name: all_by_name[name].condition for name in child_scope}
        child_memo: dict[str, int] = {}
        for c in children:
            wave_depth_by_name[c.job_name] = dependency_wave(
                c.job_name, child_conditions, child_scope, child_memo
            )
    for r in orphans:
        wave_depth_by_name[r.job_name] = 1

    for r in ordered:
        size, drivers = score_job(
            r, all_by_name, wave_depth_by_name.get(r.job_name, 1)
        )
        risk, risk_drivers = score_operational_risk(
            r, (run_stats or {}).get(r.job_name)
        )
        results.append(JobAssessment(
            job_name = r.job_name,
            job_type = (r.job_type or "CMD").upper(),
            box_name = r.box_name or "",
            size     = size,
            effort_h = EFFORT_HOURS[size],
            drivers  = "; ".join(drivers),
            risk         = risk,
            risk_drivers = "; ".join(risk_drivers),
            blast_radius = blast_radius.get(r.job_name, 0),
            gap_tags     = ", ".join(compute_gap_tags(r)),
        ))

    return results


def compute_summary(results: list[JobAssessment]) -> AssessmentSummary:
    """Compute the T-shirt size breakdown + effort estimate for *results*."""
    counts: dict[str, int] = {s: 0 for s in SIZES}
    hours:  dict[str, int] = {s: 0 for s in SIZES}
    risk_counts: dict[str, int] = {r: 0 for r in RISK_LEVELS}
    gap_severity_counts: dict[str, int] = {"RED": 0, "YELLOW": 0, "GREEN": 0}
    for rec in results:
        counts[rec.size] += 1
        hours[rec.size]  += rec.effort_h
        risk_counts[rec.risk] += 1
        for tag in (t for t in rec.gap_tags.split(", ") if t):
            severity = GAP_CATALOGUE[tag][0]
            gap_severity_counts[severity] += 1

    total_jobs = sum(counts.values())
    raw_hours  = sum(hours.values())

    platform_h = int(raw_hours * PLATFORM_OVERHEAD_PCT)
    testing_h  = int(raw_hours * TESTING_OVERHEAD_PCT)
    pm_h       = int(raw_hours * PM_OVERHEAD_PCT)
    training_h = int(raw_hours * TRAINING_OVERHEAD_PCT)
    total_h    = raw_hours + platform_h + testing_h + pm_h + training_h
    total_days = round(total_h / 8)

    return AssessmentSummary(
        counts     = counts,
        hours      = hours,
        total_jobs = total_jobs,
        raw_hours  = raw_hours,
        platform_h = platform_h,
        testing_h  = testing_h,
        pm_h       = pm_h,
        training_h = training_h,
        total_h    = total_h,
        total_days = total_days,
        risk_counts         = risk_counts,
        gap_severity_counts = gap_severity_counts,
    )
