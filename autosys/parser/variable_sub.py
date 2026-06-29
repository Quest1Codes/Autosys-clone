"""
AutoSys %%VAR%% variable substitution engine.

How it works in real AutoSys
-----------------------------
When the Scheduler ACE dispatches a CMD job it expands the command string
*just before* handing it to the System Agent.  Every ``%%TOKEN%%`` pattern is
replaced with either a built-in runtime value or a user-defined global.

Built-in (read-only, resolved at dispatch time — NOT stored in the DB)
-----------------------------------------------------------------------
%%DATE%%    MMDDYYYY  e.g. "06252026"  — the *scheduled* run date
%%YYYY%%    4-digit year               e.g. "2026"
%%MM%%      2-digit month (01-12)      e.g. "06"
%%DD%%      2-digit day   (01-31)      e.g. "25"
%%TIME%%    HHMM of the dispatch time  e.g. "0602"
%%AUTORUN%% "Y" if started by the Scheduler, "N" if FORCE_STARTJOB

User-defined
------------
Any name not in the built-in set is looked up in the ``globals`` dict (loaded
from the ``global_variables`` table).  Names are case-insensitive; they are
normalised to UPPERCASE before lookup, matching AutoSys's own behaviour.

Error policy
------------
By default, referencing an undefined variable raises ``UndefinedVariableError``.
Pass ``strict=False`` to leave unknown tokens unexpanded (they stay as
``%%VARNAME%%`` in the output).  This is useful for the JIL *parser* which
processes templates before run-time globals are available.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class UndefinedVariableError(Exception):
    """Raised when a %%VAR%% token has no value in the given context."""
    def __init__(self, name: str, template: str) -> None:
        super().__init__(
            f"Undefined AutoSys variable '%%{name}%%' "
            f"in template: {template!r}"
        )
        self.name = name
        self.template = template


# ---------------------------------------------------------------------------
# Built-in variable builder
# ---------------------------------------------------------------------------

def build_builtins(
    run_date: Optional[date] = None,
    dispatch_time: Optional[datetime] = None,
    autorun: bool = True,
) -> dict[str, str]:
    """
    Return a dict of the six AutoSys built-in variables for a given run.

    Parameters
    ----------
    run_date:
        The *logical* scheduled date of the run (not necessarily today —
        AutoSys separates "what date is this job running for" from "what time
        is it now").  Defaults to today's date.
    dispatch_time:
        Wall-clock time when the Scheduler dispatches the job.
        Used for %%TIME%%.  Defaults to ``datetime.now()``.
    autorun:
        True  → job was started by the Scheduler (normal schedule trigger).
        False → job was started via FORCE_STARTJOB (manual override).
        Maps to %%AUTORUN%% = "Y" / "N".
    """
    if run_date is None:
        run_date = date.today()
    if dispatch_time is None:
        dispatch_time = datetime.now()

    return {
        "DATE":    run_date.strftime("%m%d%Y"),      # MMDDYYYY
        "YYYY":    run_date.strftime("%Y"),           # 4-digit year
        "MM":      run_date.strftime("%m"),           # 2-digit month
        "DD":      run_date.strftime("%d"),           # 2-digit day
        "TIME":    dispatch_time.strftime("%H%M"),    # HHMM
        "AUTORUN": "Y" if autorun else "N",
    }


# ---------------------------------------------------------------------------
# Core substitution function
# ---------------------------------------------------------------------------

# Matches %%TOKEN%% where TOKEN is one or more word characters (letters,
# digits, underscores).  AutoSys variable names are A-Z0-9_ only but we
# match broadly and report unknown names clearly.
_VAR_RE = re.compile(r'%%([A-Za-z0-9_]+)%%')


def substitute(
    template: str,
    globals: Optional[dict[str, str]] = None,
    run_date: Optional[date] = None,
    dispatch_time: Optional[datetime] = None,
    autorun: bool = True,
    strict: bool = True,
) -> str:
    """
    Expand all ``%%VAR%%`` tokens in *template* and return the result.

    Resolution order
    ----------------
    1. Built-in variables (DATE, YYYY, MM, DD, TIME, AUTORUN)
    2. User-defined globals (from the ``globals`` dict, normalised to
       UPPERCASE)
    3. If still not found:
       - ``strict=True``  → raise ``UndefinedVariableError``
       - ``strict=False`` → leave token unchanged

    Parameters
    ----------
    template:
        The raw JIL attribute value, e.g.
        ``"/scripts/extract.sh --date %%DATE%%"``
    globals:
        Dict of user-defined global variable values keyed by UPPERCASE name.
        Comes from the ``global_variables`` DB table (loaded by the
        Scheduler before dispatching).
    run_date:
        Logical run date for %%DATE%%, %%YYYY%%, %%MM%%, %%DD%%.
    dispatch_time:
        Wall-clock time for %%TIME%%.
    autorun:
        Source flag for %%AUTORUN%%.
    strict:
        Whether to raise on missing variables (True) or leave them as-is.

    Returns
    -------
    str
        The template with all resolved tokens expanded.

    Examples
    --------
    >>> from datetime import date
    >>> substitute(
    ...     "/data/sales_%%DATE%%.csv",
    ...     run_date=date(2026, 6, 25),
    ... )
    '/data/sales_06252026.csv'

    >>> substitute("Hello %%NAME%%", globals={"NAME": "World"})
    'Hello World'

    >>> substitute("Hello %%MISSING%%", strict=False)
    'Hello %%MISSING%%'
    """
    builtins = build_builtins(run_date, dispatch_time, autorun)
    resolved_globals = {k.upper(): v for k, v in (globals or {}).items()}

    def _replace(match: re.Match) -> str:
        name = match.group(1).upper()

        # 1. Built-in?
        if name in builtins:
            return builtins[name]

        # 2. User global?
        if name in resolved_globals:
            return resolved_globals[name]

        # 3. Undefined
        if strict:
            raise UndefinedVariableError(name, template)
        return match.group(0)   # leave %%VARNAME%% unchanged

    return _VAR_RE.sub(_replace, template)


# ---------------------------------------------------------------------------
# Batch substitution — expand a whole job attrs dict at once
# ---------------------------------------------------------------------------

#: Attribute names whose values should be expanded.
#: (Not every attribute needs substitution — calendar names, machine names,
#: owner names etc. are static.  Only execution-time strings are expanded.)
_EXPANDABLE_ATTRS = frozenset({
    "command",
    "std_out_file",
    "std_err_file",
    "std_in_file",
    "watch_file",
    "ftp_src",
    "ftp_dest",
    "notification_msg",
})


def substitute_job_attrs(
    attrs: dict[str, str],
    globals: Optional[dict[str, str]] = None,
    run_date: Optional[date] = None,
    dispatch_time: Optional[datetime] = None,
    autorun: bool = True,
    strict: bool = True,
) -> dict[str, str]:
    """
    Return a copy of *attrs* with ``%%VAR%%`` tokens expanded in every
    expandable attribute.

    Called by the Scheduler ACE (Phase 7) just before dispatching a job
    to the System Agent.  The original attrs dict is not mutated.

    Parameters
    ----------
    attrs:
        Raw job attribute dict as produced by the JIL parser (or read from
        the ``jobs`` DB table).
    globals, run_date, dispatch_time, autorun, strict:
        Forwarded to :func:`substitute`.

    Returns
    -------
    dict[str, str]
        A new dict with the same keys but expanded values for expandable
        attributes.
    """
    result = dict(attrs)
    for key in _EXPANDABLE_ATTRS:
        if key in result and result[key]:
            result[key] = substitute(
                result[key],
                globals=globals,
                run_date=run_date,
                dispatch_time=dispatch_time,
                autorun=autorun,
                strict=strict,
            )
    return result


# ---------------------------------------------------------------------------
# Introspection helper — list all %%VAR%% tokens in a template
# ---------------------------------------------------------------------------

def list_variables(template: str) -> list[str]:
    """
    Return the unique variable names referenced in *template*, in order of
    first appearance, normalised to UPPERCASE.

    Useful for static analysis of JIL files to detect missing globals before
    job submission.

    Example
    -------
    >>> list_variables("/scripts/load.sh --date %%DATE%% --env %%ENV%%")
    ['DATE', 'ENV']
    """
    seen: dict[str, None] = {}   # ordered set
    for m in _VAR_RE.finditer(template):
        name = m.group(1).upper()
        seen[name] = None
    return list(seen)
