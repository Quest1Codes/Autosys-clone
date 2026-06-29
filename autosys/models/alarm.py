"""
Pydantic model for AutoSys alarms.

In real AutoSys, the alarm manager (also called the Alarm Management Facility,
or AMF) watches for job events that match alarm conditions and writes records to
the alarm log and to NSM (Network and Systems Management) integration endpoints.
Operators inspect active alarms via the ``autorep -A`` command or through the
AutoSys GUI alarm panel.

Alarm life-cycle
----------------
1.  A job transitions to FAILURE, TERMINATED, or a runtime-duration threshold
    (max_run_alarm / min_run_alarm) is crossed.
2.  The alarm_manager (Phase 11 in this clone) evaluates the job's alarm
    attributes (alarm_if_fail, alarm_if_terminated, max_run_alarm, min_run_alarm)
    or detects a HEARTBEAT_FAIL from the agent monitor.
3.  An ``Alarm`` record is created with ``cleared_at=None`` (active).
4.  The notifier dispatches the alarm to the configured channel
    (email / SNMP / NSM / Remedy) and sets ``notified=True``.
5.  An operator (or an automated remediation script) acknowledges the alarm,
    setting ``cleared_at`` and ``cleared_by``.

This module intentionally contains only the data model; all alarm-raising and
notification logic lives in ``alarm_manager.py`` (Phase 11).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from autosys.models.enums import AlarmType, JobStatus


class Alarm(BaseModel):
    """
    A single alarm record raised by the alarm_manager.

    Alarms are persistent records: once raised they remain active until
    explicitly cleared.  They are the primary mechanism by which AutoSys
    notifies operations teams that a job has deviated from expected behaviour.

    Mapping to real AutoSys
    -----------------------
    Real AutoSys stores alarm records in the ``EVENT_DEMON`` table (or a
    dedicated alarm table depending on the version and site configuration).
    The ``autorep -A`` command lists all active alarms; operators clear them
    via ``sendevent -E CLEAR_ALARM -J <job_name>`` or through the GUI alarm
    panel.

    This clone persists ``Alarm`` rows in the ``alarms`` database table, which
    is managed exclusively by the ``alarm_manager`` service.  The ``alarm_id``
    UUID is the primary key used for all cross-service references (e.g. in
    notification dispatch logs and audit tables).

    Field ordering follows the logical life-cycle of an alarm: identity first,
    then the origin (which job / run triggered it), then classification, then
    content, then the temporal lifecycle fields, and finally the notification
    tracking flag.
    """

    model_config = {"use_enum_values": True}

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    alarm_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description=(
            "UUID primary key for this alarm record.  Generated automatically "
            "at creation time using ``uuid.uuid4()``.  This value is immutable "
            "once the record is persisted and is used as the foreign-key "
            "reference from notification dispatch logs, acknowledgement audit "
            "records, and any downstream alerting integrations (NSM, Remedy, "
            "PagerDuty, etc.)."
        ),
    )

    # ------------------------------------------------------------------
    # Origin — which job / run triggered this alarm
    # ------------------------------------------------------------------

    job_name: str = Field(
        ...,
        description=(
            "The ``job_name`` of the AutoSys job that triggered this alarm.  "
            "Corresponds to the ``job_name`` column in the ``jobs`` table.  "
            "This value is denormalised (copied here rather than joined) so "
            "that historical alarm records remain meaningful even if the job "
            "definition is later deleted or renamed.  In real AutoSys the "
            "alarm panel always displays the originating job name; this field "
            "is the source of that display value."
        ),
    )

    run_id: Optional[str] = Field(
        None,
        description=(
            "UUID of the ``JobRun`` record (FK → ``job_runs.run_id``) that was "
            "executing when this alarm was raised.  Provides a direct link from "
            "the alarm to the full run record (stdout path, exit code, duration, "
            "machine name, etc.) for post-mortem analysis.\n"
            "\n"
            "Will be ``None`` for alarms that are not tied to a specific run "
            "instance.  The primary example is ``HEARTBEAT_FAIL``: an agent "
            "monitor alarm raised because a System Agent stopped responding, "
            "which can happen when no job is actively running on that machine."
        ),
    )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    alarm_type: AlarmType = Field(
        ...,
        description=(
            "The category of alarm.  Determines how the alarm_manager evaluates "
            "the triggering condition and how the notifier routes the alert:\n"
            "\n"
            "  ALARM_IF_FAIL\n"
            "      Raised when the job reaches FAILURE state and the job "
            "      definition has ``alarm_if_fail=True``.  The most common "
            "      alarm type; indicates the job's command exited with a "
            "      non-zero exit code after all configured retries (n_retrys) "
            "      were exhausted.  In real AutoSys JIL this corresponds to "
            "      the attribute ``alarm_if_fail: 1``.\n"
            "\n"
            "  ALARM_IF_TERMINATED\n"
            "      Raised when the job reaches TERMINATED state (i.e. was "
            "      killed by a KILLJOB event) and the job definition has "
            "      ``alarm_if_terminated=True``.  Often indicates deliberate "
            "      manual intervention by an operator or an automated runaway-"
            "      process kill policy.  In real AutoSys JIL: "
            "      ``alarm_if_terminated: 1``.\n"
            "\n"
            "  MAX_RUN_ALARM\n"
            "      Raised when a job has been continuously in RUNNING state "
            "      for longer than its ``max_run_alarm`` minutes threshold "
            "      (a watchdog timer).  Critically, the job is NOT killed "
            "      automatically — only the alarm is raised.  Operators must "
            "      investigate and optionally issue a KILLJOB event manually.  "
            "      In real AutoSys JIL: ``max_run_alarm: 90`` (minutes).\n"
            "\n"
            "  MIN_RUN_ALARM\n"
            "      Raised when a job finishes (SUCCESS or FAILURE) in less "
            "      time than its ``min_run_alarm`` minutes threshold.  This "
            "      signals unexpectedly short execution — a common data-quality "
            "      indicator where the job found no records to process (e.g. an "
            "      empty feed file) and exited quickly.  In real AutoSys JIL: "
            "      ``min_run_alarm: 5`` (minutes).\n"
            "\n"
            "  HEARTBEAT_FAIL\n"
            "      Raised by the agent monitor component when a "
            "      CHECK_HEARTBEAT event finds a System Agent unreachable "
            "      (no response within the timeout window).  This alarm is "
            "      not tied to any specific job run; ``run_id`` will be "
            "      ``None``.  In real AutoSys this corresponds to an "
            "      AGENT_UNAVAILABLE event in the alarm log."
        ),
    )

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------

    message: str = Field(
        ...,
        description=(
            "Human-readable description of why this alarm was raised.  "
            "Constructed by the alarm_manager from a template appropriate to "
            "``alarm_type``.  This message is included verbatim in email "
            "notification bodies and in NSM / Remedy alert payloads.\n"
            "\n"
            "Example messages by alarm type:\n"
            "\n"
            "  ALARM_IF_FAIL:\n"
            "    'Job extract_sales reached FAILURE after 3 retries at "
            "    2024-03-15 06:32:41 UTC'\n"
            "\n"
            "  ALARM_IF_TERMINATED:\n"
            "    'Job load_reporting_db was TERMINATED (KILLJOB received) at "
            "    2024-03-15 07:14:09 UTC'\n"
            "\n"
            "  MAX_RUN_ALARM:\n"
            "    'Job load_dwh has been RUNNING for 95 minutes, exceeding "
            "    max_run_alarm threshold of 90 minutes'\n"
            "\n"
            "  MIN_RUN_ALARM:\n"
            "    'Job process_feed finished in 2 minutes, below min_run_alarm "
            "    threshold of 10 minutes (possible empty feed)'\n"
            "\n"
            "  HEARTBEAT_FAIL:\n"
            "    'System Agent on prod-etl-01 did not respond to heartbeat "
            "    ping at 2024-03-15 05:00:03 UTC after 30s timeout'"
        ),
    )

    job_status_at_raise: Optional[JobStatus] = Field(
        None,
        description=(
            "The runtime status the job was in at the exact moment this alarm "
            "was raised.  Stored here for audit and post-mortem analysis so "
            "that the alarm record is self-contained even if the job's status "
            "changes later.\n"
            "\n"
            "Expected values per alarm type:\n"
            "  ALARM_IF_FAIL       → always FAILURE\n"
            "  ALARM_IF_TERMINATED → always TERMINATED\n"
            "  MAX_RUN_ALARM       → always RUNNING\n"
            "  MIN_RUN_ALARM       → SUCCESS or FAILURE (whichever the job "
            "                        reached before the threshold was breached)\n"
            "  HEARTBEAT_FAIL      → None (not tied to a specific job run or "
            "                        job status)"
        ),
    )

    # ------------------------------------------------------------------
    # Lifecycle timestamps
    # ------------------------------------------------------------------

    raised_at: datetime = Field(
        default_factory=datetime.utcnow,
        description=(
            "UTC timestamp at which this alarm was created by the alarm_manager.  "
            "This is the authoritative time used for SLA compliance reporting "
            "and mean-time-to-acknowledge (MTTA) calculations.  Defaulted to "
            "``datetime.utcnow()`` so that records created programmatically "
            "without an explicit timestamp are still meaningful.  In real "
            "AutoSys this corresponds to the ``ALARM_TIME`` column in the alarm "
            "log table."
        ),
    )

    cleared_at: Optional[datetime] = Field(
        None,
        description=(
            "UTC timestamp at which the alarm was acknowledged and cleared.  "
            "``None`` means the alarm is still active and has not yet been "
            "addressed.\n"
            "\n"
            "Set by the alarm_manager when any of the following occur:\n"
            "  • An operator sends a CLEAR_ALARM event:\n"
            "      sendevent -E CLEAR_ALARM -J <job_name>\n"
            "  • An automated remediation script calls the alarm clear API.\n"
            "  • The alarm_manager auto-clears the alarm because the job "
            "    subsequently completed successfully in a retry cycle "
            "    (configurable behaviour).\n"
            "\n"
            "In real AutoSys this corresponds to the GUI 'Clear Alarm' action "
            "or the ``autorep -a -J <job_name>`` acknowledge flow."
        ),
    )

    cleared_by: Optional[str] = Field(
        None,
        description=(
            "Identity of the actor that cleared this alarm.  Always set "
            "alongside ``cleared_at``; both fields are written atomically.\n"
            "\n"
            "Possible values:\n"
            "  • A username (e.g. 'jsmith') — an operator cleared it manually "
            "    via the GUI, CLI, or REST API with an authenticated session.\n"
            "  • A job_name (e.g. 'remediation_restart_job') — an automated "
            "    AutoSys job cleared it by raising a CLEAR_ALARM event as part "
            "    of its post-execution logic.\n"
            "  • 'auto' — the alarm_manager cleared it automatically because "
            "    the triggering condition resolved itself (e.g. the job "
            "    succeeded on retry, or the System Agent came back online).\n"
            "\n"
            "``None`` when the alarm has not yet been cleared."
        ),
    )

    # ------------------------------------------------------------------
    # Notification tracking
    # ------------------------------------------------------------------

    notified: bool = Field(
        False,
        description=(
            "Whether the outbound notification for this alarm has been "
            "successfully dispatched to the configured channel.  Starts as "
            "``False`` at alarm creation.  Set to ``True`` by the notifier "
            "component (Phase 12) after it receives a successful acknowledgement "
            "from the notification backend (SMTP server, NSM endpoint, Remedy "
            "API, SNMP trap receiver, etc.).\n"
            "\n"
            "The alarm_manager's retry loop checks this flag before each "
            "notification attempt so that it does not send duplicate alerts "
            "if the alarm is re-evaluated before the first notification has "
            "been confirmed.  If the notifier fails (e.g. SMTP server "
            "temporarily unreachable), this flag remains ``False`` and the "
            "retry loop will attempt to re-send on the next evaluation cycle."
        ),
    )

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Return ``True`` when this alarm has not yet been cleared.

        An alarm is considered active as long as ``cleared_at`` is ``None``.
        Once an operator (or an automated process) acknowledges the alarm and
        the alarm_manager writes a ``cleared_at`` timestamp, this property
        returns ``False`` and the alarm is treated as resolved in dashboards,
        SLA reports, and the operations runbook queue.

        In real AutoSys, ``autorep -A`` only displays active alarms by default;
        resolved alarms are visible in the historical alarm log (accessible via
        ``autorep -A -s ALL`` in some AutoSys versions).

        Returns
        -------
        bool
            ``True`` if ``self.cleared_at is None`` (alarm still open).
            ``False`` if ``self.cleared_at`` has been set (alarm resolved).
        """
        return self.cleared_at is None
