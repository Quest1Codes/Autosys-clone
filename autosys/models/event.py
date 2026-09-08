"""
Pydantic models for the AutoSys Event Queue (EPS queue).

Background
----------
In real AutoSys, almost every action that changes a job's state is driven by
an **event**.  Operators issue events via ``sendevent`` on the command line;
the Scheduler ACE generates time-triggered events internally; System Agents
emit completion events when subprocesses exit.  All of these flow through a
single FIFO queue — the **Event Processor Service (EPS)** queue — which is
consumed, in strict order, by the EPS loop inside
``scheduler_ace/event_processor.py``.

This module defines two models:

``Event``
    Represents one row in the live ``event_queue`` table.  The EPS reads
    unprocessed events (``processed=False``) in ``created_at`` order,
    transitions job states, and marks events as ``processed=True``.

``EventHistory``
    An append-only audit log entry derived from ``Event``.  Written to the
    ``event_history`` table after the EPS processes each event.  Includes
    an extra ``metadata_json`` blob for contextual data that is too
    variable-shaped to normalise into columns (e.g. the previous status
    before a CHANGE_STATUS event).

EPS processing order
~~~~~~~~~~~~~~~~~~~~
The EPS processes events **strictly in ``created_at`` ascending order**.
This ensures that, for example, a HOLD_JOB event that arrives before a
STARTJOB event is applied first — preserving the causal order an operator
intended.  Within the same microsecond, ``event_id`` (UUID) provides a
deterministic tiebreaker (string-sorted).

Event lifecycle
~~~~~~~~~~~~~~~
1. Any component (CLI, REST API, Scheduler, Agent, internal retry logic)
   creates an ``Event`` and inserts it into ``event_queue``.
2. The EPS polling loop reads ``SELECT … WHERE processed=False ORDER BY
   created_at ASC LIMIT <batch>``.
3. For each event, the EPS calls the appropriate handler in the state machine,
   updates job status, and sets ``processed=True`` + ``processed_at=<now>``.
4. The EPS copies the completed event into ``event_history`` with any extra
   audit context in ``metadata_json``, then deletes (or archives) the row
   from ``event_queue``.

Cross-field validation
~~~~~~~~~~~~~~~~~~~~~~
Not every field is applicable to every event type.  The ``@model_validator``
below enforces the same rules AutoSys enforces when you run ``sendevent``:
missing required fields surface as a ``ValueError`` with a clear message
rather than silently being ignored or causing a cryptic downstream failure.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from autosys.models.enums import EventType, EventSource, JobStatus


# ---------------------------------------------------------------------------
# Event  —  one row in the live event_queue table
# ---------------------------------------------------------------------------

class Event(BaseModel):
    """
    A single event on the AutoSys EPS (Event Processor Service) queue.

    Events are the *only* mechanism by which job states are changed.
    No component may write directly to the job status table; instead it
    posts an ``Event`` and waits for the EPS to process it.  This design
    guarantees that all state transitions are serialised, auditable, and
    reproducible.

    Real AutoSys equivalents
    ~~~~~~~~~~~~~~~~~~~~~~~~
    * ``sendevent -E STARTJOB -J <job>``            →  EventType.STARTJOB
    * ``sendevent -E FORCE_STARTJOB -J <job>``      →  EventType.FORCE_STARTJOB
    * ``sendevent -E KILLJOB -J <job>``             →  EventType.KILLJOB
    * ``sendevent -E CHANGE_STATUS -J <job> -s <s>``→  EventType.CHANGE_STATUS
    * ``sendevent -E SET_GLOBAL -G <name>=<value>`` →  EventType.SET_GLOBAL
    * ``sendevent -E HOLD_JOB -J <job>``            →  EventType.HOLD_JOB
    * ``sendevent -E JOB_OFF_HOLD -J <job>``        →  EventType.JOB_OFF_HOLD
    * ``sendevent -E JOB_ON_ICE -J <job>``          →  EventType.JOB_ON_ICE
    * ``sendevent -E JOB_OFF_ICE -J <job>``         →  EventType.JOB_OFF_ICE

    Validation summary (enforced by ``@model_validator``)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    +-------------------+------------------------------------------+
    | event_type        | required extra fields                    |
    +===================+==========================================+
    | CHANGE_STATUS     | new_status                               |
    +-------------------+------------------------------------------+
    | SET_GLOBAL        | global_name, global_value                |
    +-------------------+------------------------------------------+
    | STARTJOB          | job_name                                 |
    | FORCE_STARTJOB    | job_name                                 |
    | KILLJOB           | job_name                                 |
    | HOLD_JOB          | job_name                                 |
    | JOB_OFF_HOLD      | job_name                                 |
    | JOB_ON_ICE        | job_name                                 |
    | JOB_OFF_ICE       | job_name                                 |
    +-------------------+------------------------------------------+
    | CHECK_HEARTBEAT   | (no extra fields required)               |
    | SEND_ALERT        | (no extra fields required)               |
    +-------------------+------------------------------------------+
    """

    model_config = {"use_enum_values": True}

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description=(
            "Primary key for this event. "
            "Generated as a UUID4 string at creation time for distributed-safe "
            "uniqueness without a centralised sequence. "
            "Used as a tiebreaker when two events share the same ``created_at`` "
            "microsecond during EPS batch ordering. "
            "In real AutoSys the equivalent is the auto-increment integer primary "
            "key in the ``ujo_sendq`` Oracle table."
        ),
    )

    # ------------------------------------------------------------------
    # Event classification
    # ------------------------------------------------------------------

    event_type: EventType = Field(
        ...,
        description=(
            "The kind of action this event represents. One of 11 types: \n"
            "  STARTJOB        — Start the target job if all conditions are met. "
            "Equivalent to ``sendevent -E STARTJOB``. The EPS evaluates the "
            "condition expression before dispatching; if conditions are not met "
            "the job transitions to WAIT_REPLY instead of STARTING.\n"
            "  FORCE_STARTJOB  — Start the job immediately, bypassing all "
            "condition and calendar checks. Equivalent to ``sendevent -E "
            "FORCE_STARTJOB``. Used by operators to manually kick off a job "
            "regardless of dependency state.\n"
            "  KILLJOB         — Send SIGTERM to the running process on the "
            "System Agent, then transition the job to TERMINATED. If the process "
            "does not exit within a grace period, SIGKILL is sent.\n"
            "  HOLD_JOB        — Freeze the job in ON_HOLD state. The job will "
            "not start (even if conditions become satisfied) until a JOB_OFF_HOLD "
            "event is received.\n"
            "  JOB_OFF_HOLD    — Release a job from ON_HOLD back to INACTIVE, "
            "allowing normal scheduling and condition evaluation to resume.\n"
            "  JOB_ON_ICE      — Freeze the job for the *current run cycle* only "
            "(ON_ICE state). At the next cycle boundary the Scheduler automatically "
            "resets it to INACTIVE without requiring a JOB_OFF_ICE event.\n"
            "  JOB_OFF_ICE     — Manually release a job from ON_ICE to INACTIVE "
            "before the next cycle boundary.\n"
            "  CHANGE_STATUS   — Directly override a job's status to an arbitrary "
            "``new_status`` value. Used by operators to manually correct a stuck "
            "job (e.g. mark a FAILURE as SUCCESS to unblock downstream jobs). "
            "Requires ``new_status`` field to be set.\n"
            "  SET_GLOBAL      — Set a named AutoSys global variable to a new "
            "string value. Global variables can be tested with ``value(NAME)`` in "
            "condition expressions, allowing data-driven job dependencies. "
            "Requires ``global_name`` and ``global_value`` fields.\n"
            "  CHECK_HEARTBEAT — Instruct the Scheduler to ping a System Agent and "
            "verify it is alive. A failure raises a HEARTBEAT_FAIL alarm.\n"
            "  SEND_ALERT      — Raise a manual alarm through the alarm manager "
            "without requiring a job failure (useful for testing alert pipelines)."
        ),
    )

    # ------------------------------------------------------------------
    # Target job  (applicable to most event types)
    # ------------------------------------------------------------------

    job_name: Optional[str] = Field(
        None,
        description=(
            "Name of the AutoSys job this event targets. "
            "Required for the following event types: STARTJOB, FORCE_STARTJOB, "
            "KILLJOB, HOLD_JOB, JOB_OFF_HOLD, JOB_ON_ICE, JOB_OFF_ICE, and "
            "CHANGE_STATUS. "
            "Must match an existing entry in ``jobs.job_name``. "
            "None for SET_GLOBAL events (which target a variable, not a job) "
            "and optionally None for CHECK_HEARTBEAT / SEND_ALERT events. "
            "In real AutoSys this is the ``-J <job_name>`` argument to sendevent."
        ),
    )

    # ------------------------------------------------------------------
    # CHANGE_STATUS payload
    # ------------------------------------------------------------------

    new_status: Optional[JobStatus] = Field(
        None,
        description=(
            "The target status for a CHANGE_STATUS event. "
            "Required when event_type is CHANGE_STATUS; must be None for all "
            "other event types (the EPS ignores it if set on a non-CHANGE_STATUS "
            "event). "
            "The EPS will validate that the requested transition is legal according "
            "to the state machine before applying it. "
            "In real AutoSys this maps to the ``-s <status>`` flag of sendevent: "
            "  ``sendevent -E CHANGE_STATUS -J ETL_LOAD -s SUCCESS`` "
            "Operators use this to manually unblock downstream jobs after "
            "manually fixing whatever caused a FAILURE."
        ),
    )

    # ------------------------------------------------------------------
    # SET_GLOBAL payload
    # ------------------------------------------------------------------

    global_name: Optional[str] = Field(
        None,
        description=(
            "Name of the AutoSys global variable to set. "
            "Required when event_type is SET_GLOBAL; must be None for all other "
            "event types. "
            "Global variable names are case-sensitive strings, typically "
            "UPPER_CASE by convention (e.g. 'BATCH_DATE', 'FEED_READY_FLAG'). "
            "In real AutoSys this is the variable portion of the ``-G name=value`` "
            "argument: ``sendevent -E SET_GLOBAL -G BATCH_DATE=20240315``. "
            "Other jobs can test this variable in their condition expression using "
            "``value(BATCH_DATE) = \"20240315\"``."
        ),
    )

    global_value: Optional[str] = Field(
        None,
        description=(
            "New string value to assign to the global variable identified by "
            "``global_name``. "
            "Required when event_type is SET_GLOBAL; must be None for all other "
            "event types. "
            "AutoSys global variables are always strings — numeric comparisons in "
            "condition expressions are done lexicographically unless the value "
            "parses as a number (implementation-defined). "
            "In real AutoSys this is the value portion of ``-G name=value``: "
            "  ``sendevent -E SET_GLOBAL -G FEED_READY_FLAG=YES`` "
            "After the EPS processes this event it updates the globals table and "
            "re-evaluates conditions on all jobs that reference FEED_READY_FLAG."
        ),
    )

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    source: EventSource = Field(
        default=EventSource.INTERNAL,
        description=(
            "Which component or actor placed this event on the queue. "
            "Used for audit logging and alerting dashboards. Values: \n"
            "  SCHEDULER — the Scheduler ACE generated this event automatically "
            "(e.g. a time-triggered STARTJOB or a dependency-resolution "
            "STARTJOB after an upstream job reached SUCCESS).\n"
            "  CLI       — a human operator ran ``sendevent`` or a JIL command "
            "from the AutoSys client shell.\n"
            "  API       — the REST API server created this event in response to "
            "an authenticated HTTP request (e.g. from a CI/CD pipeline).\n"
            "  AGENT     — a System Agent sent this event to report subprocess "
            "completion, heartbeat response, or file-watch satistfaction.\n"
            "  INTERNAL  — internal retry / alarm logic within the Scheduler "
            "(e.g. the watchdog timer generating a KILLJOB after term_run_time "
            "is exceeded, or the retry manager generating a new STARTJOB after "
            "a FAILURE with n_retrys remaining)."
        ),
    )

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description=(
            "UTC timestamp at which this event was inserted into the queue. "
            "The EPS processes events in strict ascending ``created_at`` order, "
            "so this field determines causality: an event with an earlier "
            "``created_at`` is always processed before a later one, regardless "
            "of when the EPS polling loop happens to read them. "
            "In real AutoSys the equivalent is the ``run_time`` column of the "
            "``ujo_sendq`` Oracle table (stored as a Unix epoch integer)."
        ),
    )

    # ------------------------------------------------------------------
    # Optional metadata (COMMENT, ALARM, RELEASE_RESOURCE)
    # ------------------------------------------------------------------

    comment: Optional[str] = Field(
        None,
        description="Audit comment for the event history."
    )
    
    resource_name: Optional[str] = Field(
        None,
        description="Name of the virtual resource to release."
    )

    # ------------------------------------------------------------------
    # Tracking and lineage
    # ------------------------------------------------------------------

    processed: bool = Field(
        default=False,
        description=(
            "Whether the EPS has consumed and handled this event. "
            "The EPS polling loop selects only rows where ``processed=False``. "
            "After successfully applying the event's action (state transition, "
            "global variable update, etc.), the EPS sets this to ``True`` and "
            "writes ``processed_at``. "
            "Events that fail processing (e.g. target job not found) are also "
            "marked ``processed=True`` so they do not block the queue — the error "
            "is recorded in ``EventHistory.metadata_json`` instead. "
            "In real AutoSys, processed events are deleted from ``ujo_sendq`` and "
            "archived to ``ujo_job_hist``; we retain them for auditability."
        ),
    )

    processed_at: Optional[datetime] = Field(
        None,
        description=(
            "UTC timestamp at which the EPS finished handling this event. "
            "Set atomically alongside ``processed=True`` in the same database "
            "transaction to prevent a race condition where a crash between the "
            "two writes could leave an event in an inconsistent state. "
            "The delta between ``created_at`` and ``processed_at`` measures EPS "
            "queue lag — a useful operational SLI; sustained lag indicates the "
            "EPS is overloaded or blocked on a slow state-machine operation. "
            "Remains None until the EPS processes the event."
        ),
    )

    # ------------------------------------------------------------------
    # Cross-field validation
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def validate_event_fields(self) -> "Event":
        """
        Enforce AutoSys business rules for required fields per event type.

        AutoSys's ``sendevent`` command enforces these rules at the CLI layer;
        we mirror them here in the model so that events created programmatically
        (via the API or internal scheduler logic) are subject to the same
        constraints before they ever reach the queue.

        Rules
        -----
        CHANGE_STATUS
            ``new_status`` must be provided.  Without it, the EPS would have
            no idea what state to transition the job to.

        SET_GLOBAL
            Both ``global_name`` and ``global_value`` must be provided.
            An empty ``global_value`` is legal (it clears the variable), but
            it must be explicitly set to ``""`` rather than left as ``None``.

        STARTJOB, FORCE_STARTJOB, KILLJOB, HOLD_JOB, JOB_OFF_HOLD,
        JOB_ON_ICE, JOB_OFF_ICE
            ``job_name`` must be provided because these events target a
            specific job.  The EPS will 404 anyway if the job doesn't exist,
            but catching a missing ``job_name`` here gives a clearer error
            message earlier in the call stack.

        Raises
        ------
        ValueError
            If any required field for the given event_type is missing.
        """
        # Normalise to string for comparison (use_enum_values=True stores
        # the .value string, but if the model is constructed with an enum
        # member directly before validation runs the type may still be the
        # enum itself since str-enums compare equal to their .value).
        event_type_val: str = (
            self.event_type.value
            if hasattr(self.event_type, "value")
            else str(self.event_type)
        )

        # --- CHANGE_STATUS requires new_status ---------------------------------
        if event_type_val == EventType.CHANGE_STATUS.value:
            if self.new_status is None:
                raise ValueError(
                    "CHANGE_STATUS event requires 'new_status' to be set. "
                    "Example: new_status=JobStatus.SUCCESS"
                )

        # --- SET_GLOBAL requires global_name and global_value ------------------
        elif event_type_val == EventType.SET_GLOBAL.value:
            missing: list[str] = []
            if self.global_name is None:
                missing.append("global_name")
            if self.global_value is None:
                missing.append("global_value")
            if missing:
                raise ValueError(
                    f"SET_GLOBAL event requires {missing}. "
                    "Example: global_name='BATCH_DATE', global_value='20240315'"
                )

        # --- Job-targeted events require job_name ------------------------------
        elif event_type_val in {
            EventType.STARTJOB.value,
            EventType.FORCE_STARTJOB.value,
            EventType.KILLJOB.value,
            EventType.HOLD_JOB.value,
            EventType.JOB_OFF_HOLD.value,
            EventType.JOB_ON_ICE.value,
            EventType.JOB_OFF_ICE.value,
        }:
            if not self.job_name:
                raise ValueError(
                    f"{event_type_val} event requires 'job_name' to be set. "
                    "Example: job_name='ETL_LOAD_SALES'"
                )

        elif self.event_type == EventType.COMMENT:
            if not self.comment and not self.job_name:
                raise ValueError("COMMENT requires job_name and comment")
        elif self.event_type == EventType.RELEASE_RESOURCE:
            if not self.resource_name:
                raise ValueError("RELEASE_RESOURCE requires resource_name")
        return self


# ---------------------------------------------------------------------------
# EventHistory  —  append-only audit log entry
# ---------------------------------------------------------------------------

class EventHistory(Event):
    """
    An immutable audit log record derived from a processed ``Event``.

    Written to the ``event_history`` table by the EPS immediately after it
    finishes processing an ``Event``.  Unlike the live ``event_queue`` table
    (which may eventually be pruned), the history table is **never deleted
    from** — it is the permanent paper trail for every state change, operator
    action, and scheduler decision.

    In real AutoSys the equivalent is the ``ujo_job_hist`` Oracle table, which
    stores a row for every event that has ever affected a job, along with the
    job's status before and after the event.

    The ``EventHistory`` row inherits all fields from ``Event`` (so you always
    know *what* was requested and *when*) and adds ``metadata_json`` for the
    rich contextual data that varies per event type and cannot be practically
    normalised into fixed columns.

    Typical ``metadata_json`` payloads by event type
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    CHANGE_STATUS::

        {
            "previous_status": "FAILURE",
            "reason": "Manually overridden by operator after upstream data fix",
            "operator": "john.smith"
        }

    KILLJOB::

        {
            "signal_sent": "SIGTERM",
            "pid": 48291,
            "machine": "agent-prod-01",
            "exit_code": -15
        }

    STARTJOB (EPS evaluated conditions)::

        {
            "condition_expression": "success(EXTRACT_JOB) & value(FEED_FLAG)=\\"YES\\"",
            "condition_result": true,
            "evaluated_at": "2024-03-15T06:00:00.123456"
        }

    SET_GLOBAL::

        {
            "previous_value": "NO",
            "jobs_re_evaluated": ["LOAD_JOB_A", "LOAD_JOB_B"]
        }

    Processing errors::

        {
            "error": "Job 'ETL_LOAD_SALES' not found in jobs table",
            "traceback": "..."
        }
    """

    metadata_json: Optional[str] = Field(
        None,
        description=(
            "A JSON-encoded string containing supplementary context about how "
            "the EPS handled this event. "
            "Stored as a raw JSON string (rather than a dict) so that the model "
            "remains database-agnostic: both plain VARCHAR columns and native "
            "JSONB columns (PostgreSQL) can store this field without schema "
            "changes. "
            "Common payloads include: the *previous* job status before a "
            "CHANGE_STATUS event (enabling before/after audit trails); the PID "
            "and signal sent for KILLJOB events; the evaluated condition "
            "expression result for STARTJOB events; the list of jobs whose "
            "conditions were re-evaluated after a SET_GLOBAL event; and error "
            "details if the EPS encountered a non-fatal exception while "
            "processing (the event is still marked processed=True but the error "
            "is preserved here for incident investigation). "
            "Remains None for simple events where no extra context is needed "
            "(e.g. a HOLD_JOB event with no noteworthy side-effects). "
            "Consumers should use ``json.loads(metadata_json)`` with a try/except "
            "to handle the rare case where the EPS wrote malformed JSON due to "
            "an interrupted database write."
        ),
    )
