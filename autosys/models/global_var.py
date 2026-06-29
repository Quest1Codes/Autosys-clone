"""
Pydantic model for AutoSys global variables.

In real AutoSys, global variables are named string values stored in the AutoSys
database (the ``GLOBAL_VAR`` table in the event daemon's schema).  They serve
two distinct runtime purposes:

1.  Command substitution (%%VARNAME%% tokens)
    -----------------------------------------
    At the exact moment a job's command is dispatched to the System Agent, the
    Scheduler ACE expands all ``%%VARNAME%%`` tokens in the ``command`` string
    by substituting the current value of the corresponding global variable.
    For example, a job defined with::

        command: /opt/etl/load.sh --date %%RUN_DATE%% --env %%TARGET_ENV%%

    is expanded immediately before dispatch to something like::

        /opt/etl/load.sh --date 2024-03-15 --env production

    The System Agent receives and executes the already-expanded command.  The
    original unexpanded command string is stored in the job definition; the
    expanded version is recorded in the JobRun record for auditability.

    This substitution is performed by ``command_expander.py`` (Phase 6) in this
    clone.  It reads from the ``global_variables`` table AND resolves the
    built-in read-only globals listed in ``AUTOSYS_BUILTIN_GLOBALS`` below.

2.  Dependency condition predicates (value() expressions)
    -------------------------------------------------------
    Job conditions can test global variable values using the ``value()``
    predicate in the ``condition`` JIL attribute.  For example::

        condition: value(PIPELINE_ENABLED) = "Y"

    or combined with job-status conditions::

        condition: success(extract_sales) & value(TARGET_ENV) = "production"

    The ``condition_evaluator.py`` (Phase 5) resolves ``value()`` predicates
    by looking up the named variable in the ``global_variables`` table and
    comparing the stored string value.

Setting global variables
------------------------
* ``sendevent -E SET_GLOBAL -G VARNAME=value`` — operator CLI command.  This
  places a SET_GLOBAL event on the event queue; the Event Processor Service
  (EPS) dequeues it and writes the new value to the ``global_variables`` table,
  then re-evaluates any job conditions that reference the changed variable.

* A job can raise a SET_GLOBAL event as part of its completion logic by
  including ``SET_GLOBAL`` in its post-execution configuration (specific to
  this clone's extended JIL; see ``JobSetGlobalAction`` in Phase 7).

* The ``global_var_manager`` seeds default globals from application
  configuration at startup time, marking them as ``updated_by='startup'``.

Real AutoSys CLI reference
--------------------------
  autoflags -s -g VARNAME=value   # set a global variable
  autoflags -r -g VARNAME         # read a global variable's current value
  autorep   -G VARNAME            # show global variable with change history

This clone stores ``GlobalVariable`` rows in the ``global_variables`` table
and manages them via the ``global_var_manager`` service (Phase 7).  The table
is keyed on ``name`` (always stored in UPPERCASE — see the ``uppercase_name``
validator).  All writes are upserts: if a variable with the same name already
exists, its ``value``, ``updated_at``, and ``updated_by`` fields are updated
in place rather than inserting a duplicate.

Built-in read-only globals
--------------------------
AutoSys defines a set of date/time globals that are substituted into command
strings at dispatch time.  These are NOT stored in the ``global_variables``
table — they are resolved dynamically by ``command_expander.py`` at the moment
the command string is expanded.  They are documented in the
``AUTOSYS_BUILTIN_GLOBALS`` mapping at the bottom of this module.

Attempting to read them from the ``global_variables`` table will return no
rows.  In real AutoSys, attempting to overwrite them with ``SET_GLOBAL`` is
silently ignored.  This clone's ``global_var_manager`` enforces a stricter
policy: it raises a ``ValueError`` if a caller tries to persist a
``GlobalVariable`` whose ``name`` matches a key in ``AUTOSYS_BUILTIN_GLOBALS``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class GlobalVariable(BaseModel):
    """
    A single AutoSys global variable record.

    ``GlobalVariable`` instances live in the ``global_variables`` table, one
    row per variable name.  The ``name`` column is the primary key.

    All writes are upserts: if a row with the same ``name`` already exists,
    the ``global_var_manager`` updates ``value``, ``updated_at``, and
    ``updated_by`` rather than inserting a new row.  This preserves the
    simplicity of the data model (a global variable has exactly one current
    value) while still capturing the full audit trail through a separate
    ``global_var_history`` table (managed by Phase 7).

    Case sensitivity
    ----------------
    AutoSys global variable names are case-insensitive at the CLI and JIL
    levels — ``%%run_date%%``, ``%%Run_Date%%``, and ``%%RUN_DATE%%`` all
    refer to the same variable.  This clone stores and looks up names
    exclusively in UPPERCASE to enforce a single canonical form, prevent
    duplicate-variable bugs caused by case differences, and maintain
    compatibility with real AutoSys's internal representation.

    The ``uppercase_name`` field_validator (below) performs this normalisation
    automatically, so callers never need to manually uppercase names before
    constructing a ``GlobalVariable``.

    Mapping to real AutoSys
    -----------------------
    Real AutoSys stores globals in the ``GLOBAL_VAR`` table of the event
    daemon's database schema.  The ``autoflags`` command is the canonical
    read/write interface.  The Scheduler's command-expansion step (analogous to
    ``command_expander.py`` in this clone) queries this table, together with
    the built-in globals, to perform ``%%VAR%%`` substitution in job commands.
    """

    model_config = {"use_enum_values": True}

    # ------------------------------------------------------------------
    # Identity and value
    # ------------------------------------------------------------------

    name: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_]+$",
        description=(
            "Variable name.  By convention and by enforcement (via the "
            "``uppercase_name`` field_validator below) this is always stored "
            "in UPPERCASE.  Input in any case is automatically normalised.\n"
            "\n"
            "In JIL commands, a global variable is referenced using the "
            "double-percent syntax:  ``%%NAME%%``.  For example:\n"
            "  command: /opt/etl/load.sh --date %%RUN_DATE%% --env %%TARGET_ENV%%\n"
            "\n"
            "In ``condition`` expressions, a global variable's value is tested "
            "using the ``value()`` predicate:\n"
            "  condition: value(PIPELINE_ENABLED) = \"Y\"\n"
            "  condition: success(job_a) & value(TARGET_ENV) = \"production\"\n"
            "\n"
            "Common examples of application-managed globals:\n"
            "  'RUN_DATE'         — the logical processing date for this batch\n"
            "  'TARGET_ENV'       — 'development', 'staging', or 'production'\n"
            "  'PIPELINE_ENABLED' — 'Y' or 'N' feature flag\n"
            "  'BATCH_ID'         — incrementing sequence number for audit\n"
            "  'LAST_SUCCESS_DT'  — ISO date of the last fully successful run\n"
            "\n"
            "Allowed characters: letters (A–Z, a–z), digits (0–9), underscores "
            "(_).  Spaces, hyphens, dots, and other punctuation are not "
            "permitted because they would create ambiguity within the "
            "``%%VARNAME%%`` substitution syntax."
        ),
    )

    value: str = Field(
        ...,
        description=(
            "Current string value of the global variable.  AutoSys global "
            "variables are always strings — there are no integer, boolean, or "
            "date types at the AutoSys level.  All values are stored and "
            "compared as plain text.\n"
            "\n"
            "Implications of the string-only type:\n"
            "  • Numeric comparisons in ``value()`` condition expressions are "
            "    lexicographic, not arithmetic.  '9' > '10' in a lexicographic "
            "    comparison.  Use zero-padded strings ('09', '10') if numeric "
            "    ordering matters in conditions.\n"
            "  • Boolean flags are conventionally stored as 'Y' / 'N' or "
            "    'TRUE' / 'FALSE'.  The condition_evaluator does exact string "
            "    comparison: ``value(FLAG) = \"Y\"``.\n"
            "  • Dates are conventionally stored in ISO-8601 format "
            "    ('YYYY-MM-DD') for readability, or in AutoSys's own MMDDYYYY "
            "    format to match the built-in %%DATE%% variable.\n"
            "\n"
            "The value is substituted verbatim into the command string wherever "
            "the ``%%NAME%%`` token appears.  No quoting or shell-escaping is "
            "performed by the Scheduler or the command_expander — the job "
            "author is responsible for ensuring the value is safe for the "
            "target shell and for quoting it in the command template if needed.\n"
            "\n"
            "Representative value examples:\n"
            "  name='RUN_DATE',          value='2024-03-15'\n"
            "  name='TARGET_ENV',        value='production'\n"
            "  name='PIPELINE_ENABLED',  value='Y'\n"
            "  name='MAX_PARALLEL_JOBS', value='8'\n"
            "  name='FEED_FILE_PATH',    value='/data/feeds/sales_20240315.csv'"
        ),
    )

    # ------------------------------------------------------------------
    # Audit metadata
    # ------------------------------------------------------------------

    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description=(
            "UTC timestamp of the most recent write to this variable, "
            "including the initial creation.  Updated every time the value "
            "changes.  Useful for:\n"
            "  • Debugging stale-value issues (e.g. confirming that a "
            "    SET_GLOBAL event was processed before a dependent job ran).\n"
            "  • Audit trails in SOX- or PCI-regulated environments where "
            "    changes to processing parameters must be traceable.\n"
            "  • Cache invalidation: the Scheduler can compare ``updated_at`` "
            "    against its in-memory cached value to decide whether a "
            "    database re-read is necessary."
        ),
    )

    updated_by: Optional[str] = Field(
        None,
        description=(
            "Identity of the actor that last set or modified this variable.  "
            "Written by the ``global_var_manager`` each time a SET_GLOBAL "
            "event is processed.  Possible values:\n"
            "\n"
            "  • A username (e.g. 'jsmith', 'ops_team_acct') — an operator "
            "    used the CLI:\n"
            "      sendevent -E SET_GLOBAL -G VARNAME=value\n"
            "    or the REST API with an authenticated user session.\n"
            "\n"
            "  • A job_name (e.g. 'compute_run_date', 'set_pipeline_flags') — "
            "    an AutoSys job wrote the value by raising a SET_GLOBAL event "
            "    as a post-execution action.  This is how one job can pass "
            "    computed values to downstream jobs without file I/O.\n"
            "\n"
            "  • 'startup' — the ``global_var_manager`` seeded this value "
            "    from the application's default configuration at service "
            "    startup time (e.g. populating TARGET_ENV from an environment "
            "    variable or a config file).\n"
            "\n"
            "  • 'api' — the value was set via the REST API using a service "
            "    account or API key that did not provide a human username "
            "    (e.g. a CI/CD pipeline call).\n"
            "\n"
            "  ``None`` — the record was created programmatically without "
            "    specifying an actor identity (e.g. in unit tests or data "
            "    migration scripts)."
        ),
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("name", mode="after")
    @classmethod
    def uppercase_name(cls, v: str) -> str:
        """Normalise the variable name to UPPERCASE.

        AutoSys global variable names are case-insensitive at every external
        interface — the CLI (``autoflags``), the JIL parser (``%%varname%%``),
        and the condition evaluator (``value(varname)``).  Internally, real
        AutoSys stores and compares names in UPPERCASE.

        This clone enforces the same convention: the ``name`` field is always
        stored in UPPERCASE regardless of what case the caller provides.  This
        prevents duplicate-variable problems caused by ``RUN_DATE`` and
        ``run_date`` being treated as different variables in the database while
        referring to the same substitution target in JIL.

        This validator runs in ``mode='after'``, which means Pydantic has
        already applied the ``pattern`` constraint (``^[A-Za-z0-9_]+$``) and
        confirmed the value is a valid string before this method executes.
        The uppercased result replaces the original value in the model instance.

        Parameters
        ----------
        v:
            The already pattern-validated variable name string, in any mix of
            upper- and lowercase letters.

        Returns
        -------
        str
            ``v.upper()`` — the canonical UPPERCASE form of the variable name.

        Examples
        --------
        GlobalVariable(name='run_date',  value='2024-03-15').name  → 'RUN_DATE'
        GlobalVariable(name='Run_Date',  value='2024-03-15').name  → 'RUN_DATE'
        GlobalVariable(name='RUN_DATE',  value='2024-03-15').name  → 'RUN_DATE'
        """
        return v.upper()


# ---------------------------------------------------------------------------
# Built-in read-only AutoSys global variables
# ---------------------------------------------------------------------------

# AutoSys provides a small set of date/time variables that are resolved
# dynamically at command-expansion time and substituted as %%DATE%%, %%YYYY%%,
# %%MM%%, %%DD%%, %%TIME%%, and %%AUTORUN%% in job command strings.
#
# IMPORTANT: These variables are NOT stored in the ``global_variables`` table
# and are NOT represented as ``GlobalVariable`` instances.  They are resolved
# at the moment the command_expander utility (Phase 6) processes a job's
# command string, immediately before the expanded command is dispatched to the
# System Agent.
#
# Attempting to read them from the ``global_variables`` table will return no
# rows.  In real AutoSys, attempting to write them via SET_GLOBAL is silently
# ignored.  In this clone, the ``global_var_manager`` raises a ``ValueError``
# if asked to persist a ``GlobalVariable`` whose ``name`` matches any key in
# this dictionary.
#
# This dict is the authoritative list of reserved names that the
# global_var_manager uses for its validation guard.  The string values are
# human-readable descriptions used in error messages and developer documentation
# — they are not the actual runtime values (those are computed at dispatch time).

AUTOSYS_BUILTIN_GLOBALS: dict[str, str] = {
    "DATE": (
        "Current date in MMDDYYYY format, e.g. '03152024' for March 15 2024.  "
        "Resolved to the Scheduler's current logical date at job-dispatch time.  "
        "Used in commands as %%DATE%%.  "
        "Read-only — not stored in global_variables table."
    ),
    "YYYY": (
        "4-digit calendar year of the current Scheduler logical date, "
        "e.g. '2024'.  "
        "Resolved at job-dispatch time.  "
        "Used in commands as %%YYYY%%.  "
        "Read-only — not stored in global_variables table."
    ),
    "MM": (
        "2-digit calendar month of the current Scheduler logical date, "
        "zero-padded, e.g. '03' for March and '12' for December.  "
        "Resolved at job-dispatch time.  "
        "Used in commands as %%MM%%.  "
        "Read-only — not stored in global_variables table."
    ),
    "DD": (
        "2-digit calendar day of the current Scheduler logical date, "
        "zero-padded, e.g. '05' for the 5th and '31' for the 31st.  "
        "Resolved at job-dispatch time.  "
        "Used in commands as %%DD%%.  "
        "Read-only — not stored in global_variables table."
    ),
    "TIME": (
        "Current wall-clock time in 24-hour HHMM format at the exact moment "
        "the command string is being expanded, e.g. '0630' for 6:30 AM and "
        "'1430' for 2:30 PM.  Resolved to the actual system clock time "
        "immediately before dispatch to the System Agent (not the scheduled "
        "start time).  "
        "Used in commands as %%TIME%%.  "
        "Read-only — not stored in global_variables table."
    ),
    "AUTORUN": (
        "Indicates whether the Scheduler ACE is currently processing time "
        "triggers in automatic mode.  Value is 'Y' when the ACE is running "
        "normally, or 'N' when the ACE has been paused (e.g. via the "
        "'autosyslog -off' command during a maintenance window).  "
        "Job conditions can gate on this value: "
        "value(AUTORUN) = \"Y\" prevents a job from starting when the "
        "Scheduler is in manual / paused mode.  "
        "Used in commands as %%AUTORUN%%.  "
        "Read-only — not stored in global_variables table."
    ),
}
