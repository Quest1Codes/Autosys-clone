"""
Pydantic model for AutoSys named calendars.

In real AutoSys, a calendar is a named, ordered list of dates stored in the
AutoSys database.  Job definitions reference calendars through two JIL
attributes:

  ``run_calendar: <name>``
      The job is ONLY eligible to run on the dates listed in this calendar,
      even if ``days_of_week`` and ``start_times`` would otherwise permit it.
      Used for month-end jobs, quarter-close jobs, ad-hoc one-off runs, or any
      schedule that cannot be expressed as a simple day-of-week + time pattern.

  ``exclude_calendar: <name>``
      The job is explicitly SKIPPED on the dates in this calendar, even when
      ``days_of_week`` and ``start_times`` match.  Commonly used for public
      holidays, site-wide maintenance windows, or blackout periods imposed by
      downstream systems.

The two attributes are independent and can both be set on the same job.  The
Scheduler's time-trigger evaluator resolves them in this order:

  1. Is today in ``run_calendar``?      (must be True if run_calendar is set)
  2. Is today in ``exclude_calendar``?  (must be False if exclude_calendar is set)
  3. Does today match ``days_of_week``? (must be True if days_of_week is set)

All three conditions must pass for a time-triggered STARTJOB event to fire.

Real AutoSys CLI
----------------
  cacreate  -c us_holidays -f us_holidays.cal   # create calendar from file
  caedit    -c us_holidays -f us_holidays.cal   # replace date list
  cadestroy -c us_holidays                      # delete calendar
  calist    -c us_holidays                      # list dates in calendar

Calendar files (.cal)
---------------------
AutoSys allows exporting and importing calendars via plain-text ``.cal`` files.
Each non-comment line contains a date and an optional trailing inline comment
separated by whitespace.  The ``Calendar.load_from_file()`` classmethod
implements this format.

Example .cal file::

    # us_holidays.cal — US Federal Public Holidays 2024
    #
    2024-01-01  # New Year's Day
    2024-01-15  # Martin Luther King Jr. Day
    2024-02-19  # Presidents' Day
    2024-05-27  # Memorial Day
    2024-06-19  # Juneteenth National Independence Day
    2024-07-04  # Independence Day
    2024-09-02  # Labor Day
    2024-11-28  # Thanksgiving Day
    2024-12-25  # Christmas Day

This clone stores ``Calendar`` rows in the ``calendars`` database table and
manages them via the ``calendar_manager`` service (Phase 9).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class Calendar(BaseModel):
    """
    A named AutoSys calendar — a labelled set of specific calendar dates.

    A ``Calendar`` instance is a standalone definition persisted in the
    ``calendars`` database table.  Job definitions (``Job.run_calendar`` and
    ``Job.exclude_calendar``) reference calendars by name.  The Scheduler's
    time-trigger evaluator (``scheduler_ace/time_trigger.py``, Phase 2)
    resolves calendar names to ``Calendar`` objects at runtime to decide
    whether the current date is an eligible run date for a given job.

    The same ``Calendar`` model serves both ``run_calendar`` and
    ``exclude_calendar`` semantics.  The interpretation — "run only on these
    dates" versus "skip on these dates" — is determined entirely by which
    ``Job`` attribute references the calendar, not by anything stored in the
    ``Calendar`` itself.

    Mapping to real AutoSys
    -----------------------
    Real AutoSys:  ``cacreate -c business_days -f business_days.cal``
    This clone:    ``POST /api/v1/calendars``  with a ``Calendar`` JSON body,
                   or ``calendar_manager.create_calendar(calendar)``.

    The ``calendar_name`` is the lookup key.  If a job references a calendar
    name that does not exist in the ``calendars`` table, the Scheduler logs a
    warning and treats the condition as unsatisfied (no STARTJOB event fires).
    """

    model_config = {"use_enum_values": True}

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    calendar_name: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_-]+$",
        description=(
            "Unique name that identifies this calendar in the database and in "
            "JIL definitions.  This name is used verbatim as the value of the "
            "``run_calendar`` and ``exclude_calendar`` JIL attributes.\n"
            "\n"
            "Examples: 'us_holidays', 'uk_bank_holidays', 'business_days', "
            "'month_end_dates', 'q4_processing_days', 'maintenance_windows'.\n"
            "\n"
            "Allowed characters: letters (A–Z, a–z), digits (0–9), underscores "
            "(_), and hyphens (-).  This matches the naming constraints that "
            "real AutoSys imposes on calendar names in its ``cacreate`` utility. "
            "Spaces and dots are not allowed."
        ),
    )

    # ------------------------------------------------------------------
    # Date list
    # ------------------------------------------------------------------

    dates: List[date] = Field(
        default_factory=list,
        description=(
            "Ordered list of specific calendar dates.  The interpretation of "
            "this list depends on the JIL attribute that references this "
            "calendar:\n"
            "\n"
            "  ``run_calendar``\n"
            "      These are the ONLY dates on which the job is eligible to "
            "      receive a time-triggered STARTJOB event.  If today's date "
            "      is not present in this list, the Scheduler will not fire "
            "      the event, regardless of whether ``days_of_week`` and "
            "      ``start_times`` match.\n"
            "\n"
            "  ``exclude_calendar``\n"
            "      These are dates to SKIP.  If today's date is present in "
            "      this list, the Scheduler will suppress the STARTJOB event "
            "      that would otherwise fire based on ``days_of_week`` and "
            "      ``start_times``.\n"
            "\n"
            "Dates are stored as Python ``datetime.date`` objects (year, month, "
            "day only — no time component and no timezone).  The "
            "``parse_dates`` field_validator below accepts them as:\n"
            "  • A list of 'YYYY-MM-DD' strings.\n"
            "  • A list of ``datetime.date`` objects.\n"
            "  • A single bulk string (newline- or comma-separated) for loading "
            "    from .cal files."
        ),
    )

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    description: Optional[str] = Field(
        None,
        description=(
            "Free-text explanation of the calendar's purpose and scope.  "
            "Not used by the Scheduler or any runtime component; this field "
            "exists purely for human documentation.\n"
            "\n"
            "Examples:\n"
            "  'US Federal Public Holidays for calendar year 2024'\n"
            "  'Month-end processing trigger dates, Q1–Q4 2024'\n"
            "  'Planned maintenance windows — coordinate with Infra team'"
        ),
    )

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description=(
            "UTC timestamp when this calendar record was first created in the "
            "``calendars`` table.  Set automatically on initial insertion and "
            "never modified afterwards.  Useful for auditing when a calendar "
            "was introduced and by which process or operator."
        ),
    )

    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description=(
            "UTC timestamp of the most recent modification to this calendar "
            "record.  The ``calendar_manager`` updates this field each time "
            "the ``dates`` list or any metadata field is changed (e.g. when "
            "dates are added or removed, or when the description is revised).  "
            "Can be used to detect stale cached copies held by the Scheduler "
            "and trigger a reload."
        ),
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("dates", mode="before")
    @classmethod
    def parse_dates(cls, v: object) -> list[date]:
        """Normalise the ``dates`` input into a list of ``datetime.date`` objects.

        This validator runs before Pydantic's own type coercion (``mode='before'``)
        so it can handle all supported input formats and return a list that
        Pydantic can then validate as ``List[date]``.

        Accepted input formats
        ----------------------
        1. **A list of ``'YYYY-MM-DD'`` strings** — each element is an ISO-8601
           date string.  Each is parsed with ``date.fromisoformat()``.

        2. **A list of ``datetime.date`` objects** — passed through unchanged.
           This is the normal code-path when the value arrives from the database
           layer or is constructed programmatically.

        3. **A mixed list** — a list containing a mix of ``date`` objects and
           ``'YYYY-MM-DD'`` strings.  Each element is handled individually.

        4. **A single bulk string** — treated as the text body of a ``.cal``
           file or as a comma-separated / newline-separated sequence of dates.
           Processing rules for the bulk string:
             * The string is split on newlines (``\\n``) and commas (``,``).
             * Each resulting token is stripped of leading and trailing
               whitespace.
             * Tokens that are empty after stripping are discarded.
             * Tokens whose first non-whitespace character is ``#`` are
               treated as comment lines and discarded entirely.
             * For each remaining token, the first whitespace-delimited word
               is extracted (discarding any inline comment that follows, e.g.
               ``'2024-01-01  # New Year'`` → ``'2024-01-01'``).
             * The extracted word is parsed with ``date.fromisoformat()``.

        Parameters
        ----------
        v:
            Raw input value for the ``dates`` field, in any of the formats
            described above.

        Returns
        -------
        list[date]
            A list of ``datetime.date`` objects ready for Pydantic's
            ``List[date]`` type validation step.

        Raises
        ------
        ValueError
            If ``v`` is neither a string nor a list.
            If any individual token or list item cannot be parsed as a valid
            ISO-8601 date in ``YYYY-MM-DD`` format.
        """
        if isinstance(v, str):
            # Bulk string: split on both newlines and commas; strip comments.
            raw_tokens: list[str] = []
            for segment in re.split(r"[\n,]", v):
                segment = segment.strip()
                # Skip empty lines and comment-only lines.
                if not segment or segment.startswith("#"):
                    continue
                # Extract the first whitespace-delimited word, which is the date
                # token; everything after the first whitespace is an inline comment.
                token = segment.split()[0]
                raw_tokens.append(token)
            return [date.fromisoformat(t) for t in raw_tokens]

        if isinstance(v, list):
            result: list[date] = []
            for item in v:
                if isinstance(item, date):
                    # Already a date object — pass through as-is.
                    result.append(item)
                elif isinstance(item, str):
                    result.append(date.fromisoformat(item.strip()))
                else:
                    raise ValueError(
                        f"Each element of 'dates' must be a date object or a "
                        f"'YYYY-MM-DD' string; received {type(item).__name__!r}."
                    )
            return result

        raise ValueError(
            f"'dates' must be a list of date/string items or a newline- / "
            f"comma-separated string of 'YYYY-MM-DD' values; "
            f"received {type(v).__name__!r}."
        )

    # ------------------------------------------------------------------
    # Instance methods
    # ------------------------------------------------------------------

    def contains(self, d: date) -> bool:
        """Return ``True`` when *d* is present in this calendar's date list.

        This is the primary query method used by the Scheduler's time-trigger
        evaluator when checking both ``run_calendar`` and ``exclude_calendar``
        constraints for a job that is about to fire.

        Usage in the Scheduler
        ----------------------
        For a job with ``run_calendar = 'month_end_dates'``::

            calendar = calendar_manager.get("month_end_dates")
            if not calendar.contains(today):
                return  # today is not a designated run date — skip

        For a job with ``exclude_calendar = 'us_holidays'``::

            calendar = calendar_manager.get("us_holidays")
            if calendar.contains(today):
                return  # today is a holiday — suppress the event

        Comparison semantics
        --------------------
        The comparison is an exact membership test against ``self.dates`` using
        Python's ``in`` operator.  Only the year, month, and day are considered;
        time-of-day is irrelevant because ``self.dates`` contains ``date``
        objects (no time component).

        Parameters
        ----------
        d:
            The date to look up.  Typically ``datetime.utcnow().date()`` or
            the Scheduler's current logical date.

        Returns
        -------
        bool
            ``True`` if *d* is in ``self.dates``, ``False`` otherwise.
        """
        return d in self.dates

    @classmethod
    def load_from_file(cls, path: str) -> Calendar:
        """Load a ``Calendar`` from an AutoSys-format ``.cal`` file on disk.

        This classmethod provides a convenient entry point for importing
        calendar definitions from the plain-text ``.cal`` file format that
        real AutoSys uses with the ``cacreate -f <file>`` command.

        File format specification
        -------------------------
        * Lines whose first non-whitespace character is ``#`` are comments
          and are ignored completely.
        * Blank lines (empty or whitespace-only) are ignored.
        * Every non-comment, non-blank line MUST begin with a date in
          ``YYYY-MM-DD`` format.
        * Anything after the first whitespace on a data line is treated as an
          inline comment and is discarded.  This allows annotating each date
          with a human-readable label (e.g. the holiday name) without
          affecting parsing.

        Example file (saved as ``us_holidays.cal``)::

            # us_holidays.cal
            # US Federal Public Holidays for calendar year 2024.
            # Generated by HR Operations on 2023-11-01.
            #
            2024-01-01  # New Year's Day
            2024-01-15  # Martin Luther King Jr. Day
            2024-02-19  # Presidents' Day
            2024-05-27  # Memorial Day
            2024-06-19  # Juneteenth National Independence Day
            2024-07-04  # Independence Day
            2024-09-02  # Labor Day
            2024-11-28  # Thanksgiving Day
            2024-11-29  # Day after Thanksgiving (company-observed)
            2024-12-24  # Christmas Eve (company-observed)
            2024-12-25  # Christmas Day

        Calendar name derivation
        ------------------------
        The ``calendar_name`` of the returned ``Calendar`` is derived from the
        file's stem — the filename without its directory path and without its
        extension.  For example:

          * ``/etc/autosys/calendars/us_holidays.cal``  → name ``'us_holidays'``
          * ``./month_end_dates.cal``                   → name ``'month_end_dates'``
          * ``C:\\autosys\\uk_bank_holidays.CAL``       → name ``'uk_bank_holidays'``

        This mirrors the behaviour of real AutoSys's ``cacreate`` command,
        which derives the calendar name from the filename when the ``-c`` flag
        is not provided.

        All date parsing is delegated to the ``parse_dates`` field_validator,
        ensuring consistent error handling regardless of whether dates are
        loaded from a file, a list, or a raw string.

        Parameters
        ----------
        path:
            Absolute or relative file-system path to the ``.cal`` file to read.
            Both ``str`` and ``pathlib.Path`` values are accepted.

        Returns
        -------
        Calendar
            A fully constructed ``Calendar`` instance populated with the dates
            parsed from the file.  ``created_at`` and ``updated_at`` are both
            set to ``datetime.utcnow()`` at construction time.

        Raises
        ------
        FileNotFoundError
            If *path* does not point to an existing file.
        ValueError
            If any non-comment, non-blank line in the file contains a first
            token that cannot be parsed as a valid ``YYYY-MM-DD`` date.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(
                f"AutoSys calendar file not found: {path!r}"
            )

        calendar_name = file_path.stem
        raw_text = file_path.read_text(encoding="utf-8")

        # Delegate all parsing to the parse_dates field_validator by passing
        # the raw file text directly as the 'dates' value.  The validator
        # handles comment stripping, blank-line skipping, and ISO-8601 parsing.
        return cls(calendar_name=calendar_name, dates=raw_text)
