"""
All enumerations used across the AutoSys clone.

Every enum maps 1-to-1 to a real AutoSys concept.  String values match
the exact strings AutoSys uses so that JIL files and DB records are
human-readable and directly comparable to real AutoSys output.
"""

from __future__ import annotations

from enum import Enum, IntEnum


# ---------------------------------------------------------------------------
# Job Types
# ---------------------------------------------------------------------------

class JobType(str, Enum):
    """The job_type field in a JIL definition.

    BOX       - A container/workflow that groups other jobs.
                It activates on schedule; children run inside it.
    CMD       - Runs a shell command on a target machine via the System Agent.
    FTP       - Transfers files between machines using FTP/SFTP.
    FILEWATCH - Watches for a file to appear / reach a minimum size.
    CONNECT   - Tests network connectivity to a host:port.
    """
    BOX       = "BOX"
    CMD       = "CMD"
    FTP       = "FTP"
    FILEWATCH = "FILEWATCH"
    CONNECT   = "CONNECT"
    SAP       = "SAP"
    PEOPLESOFT = "PEOPLESOFT"
    INFORMATICA = "INFORMATICA"
    MICROFOCUS = "MICROFOCUS"
    WEBSERVICE = "WEBSERVICE"
    REMOTECMD = "REMOTECMD"
    WOL       = "WOL"
    USERDEFINED = "USERDEFINED"


# ---------------------------------------------------------------------------
# Job / Run Status  (the full 12-state AutoSys state machine)
# ---------------------------------------------------------------------------

class JobStatus(IntEnum):
    """Current runtime state of a job.

    Transitions are enforced by state_machine.py (Phase 3).

    INACTIVE              - Default state; job has not yet been triggered
                            for the current run cycle.
    WAIT_REPLY            - Waiting for an external event or dependency.
    ON_HOLD               - Manually frozen by HOLD_JOB event; will not
                            start until JOB_OFF_HOLD is sent.
    ON_ICE                - Frozen for the entire current run cycle; resets
                            to INACTIVE at the next cycle boundary.
    STARTING              - ACE has dispatched the job to the System Agent
                            but the agent has not yet confirmed start.
    RUNNING               - System Agent is actively executing the job.
    SUCCESS               - Job completed with exit code 0.
    FAILURE               - Job completed with non-zero exit code and all
                            retries are exhausted.
    TERMINATED            - Job was killed by a KILLJOB event.
    RESTART               - Job failed but retries remain; ACE will
                            re-dispatch shortly.
    REFRESH_DEPENDENCIES  - ACE is re-evaluating the job's condition
                            expression after a dependency changed state.
    ACTIVATED             - BOX-only: the BOX is open and children are
                            eligible to run.
    QUE_WAIT              - Job is ready to run but is blocked waiting
                            for a virtual resource slot (max_load).
    PEND_MACH             - Waiting for a machine to become available.
    RESWAIT               - Waiting for resource.
    ON_NOEXEC             - Job bypassed execution.
    SUSPENDED             - Job suspended.
    """
    RUNNING              = 1
    STARTING             = 3
    SUCCESS              = 4
    FAILURE              = 5
    TERMINATED           = 6
    ON_ICE               = 7
    INACTIVE             = 8
    ACTIVATED            = 9
    RESTART              = 10
    ON_HOLD              = 11
    QUE_WAIT             = 12
    WAIT_REPLY           = 13
    PEND_MACH            = 14
    RESWAIT              = 15
    ON_NOEXEC            = 16
    SUSPENDED            = 17
    
    # Internal virtual state for state machine transitions
    REFRESH_DEPENDENCIES = 99


# ---------------------------------------------------------------------------
# Event Types  (used by sendevent and the Event Processor)
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """All event types that can be placed on the event queue.

    The Event Processor Service (EPS) in scheduler_ace/event_processor.py
    consumes these and drives the state machine.

    STARTJOB        - Start the job if all conditions are satisfied.
    FORCE_STARTJOB  - Start the job immediately, ignoring conditions.
    KILLJOB         - Send SIGTERM to the running process → TERMINATED.
    HOLD_JOB        - Transition job to ON_HOLD.
    JOB_OFF_HOLD    - Release a job from ON_HOLD → INACTIVE.
    JOB_ON_ICE      - Transition job to ON_ICE for this run cycle.
    JOB_OFF_ICE     - Release a job from ON_ICE → INACTIVE.
    CHANGE_STATUS   - Manually override a job's status (used by operators).
    SET_GLOBAL      - Set a named global variable (can trigger value()
                      conditions on other jobs).
    CHECK_HEARTBEAT - Ping a System Agent and verify it is reachable.
    SEND_ALERT      - Raise an alarm manually without a job failure.
    """
    STARTJOB        = "STARTJOB"
    FORCE_STARTJOB  = "FORCE_STARTJOB"
    KILLJOB         = "KILLJOB"
    CHANGE_STATUS   = "CHANGE_STATUS"
    SET_GLOBAL      = "SET_GLOBAL"
    HOLD_JOB        = "HOLD_JOB"
    JOB_ON_ICE      = "JOB_ON_ICE"
    JOB_OFF_HOLD    = "JOB_OFF_HOLD"
    JOB_OFF_ICE     = "JOB_OFF_ICE"
    ALARM           = "ALARM"
    COMMENT         = "COMMENT"
    REPLY_RESPONSE  = "REPLY_RESPONSE"
    RELEASE_RESOURCE = "RELEASE_RESOURCE"
    CHECK_HEARTBEAT = "CHECK_HEARTBEAT"
    SEND_ALERT      = "SEND_ALERT"


# ---------------------------------------------------------------------------
# Alarm Types
# ---------------------------------------------------------------------------

class AlarmType(str, Enum):
    """Categories of alarms raised by the alarm_manager.

    ALARM_IF_FAIL        - job_attribute: alarm_if_fail = 1
    ALARM_IF_TERMINATED  - job attribute: alarm_if_terminated = 1
    MAX_RUN_ALARM        - job has been running longer than max_run_alarm
                           minutes (watchdog timer).
    MIN_RUN_ALARM        - job finished in less than min_run_alarm minutes
                           (unexpectedly short run — data quality signal).
    HEARTBEAT_FAIL       - System Agent did not respond to CHECK_HEARTBEAT.
    """
    ALARM_IF_FAIL       = "ALARM_IF_FAIL"
    ALARM_IF_TERMINATED = "ALARM_IF_TERMINATED"
    MAX_RUN_ALARM       = "MAX_RUN_ALARM"
    MIN_RUN_ALARM       = "MIN_RUN_ALARM"
    HEARTBEAT_FAIL      = "HEARTBEAT_FAIL"


# ---------------------------------------------------------------------------
# Days of Week  (maps directly to JIL days_of_week values)
# ---------------------------------------------------------------------------

class DayOfWeek(str, Enum):
    """Allowed tokens in the JIL days_of_week attribute.

    Example JIL:  days_of_week: mo,tu,we,th,fr
    Special value ALL means every day of the week.
    """
    MO  = "mo"
    TU  = "tu"
    WE  = "we"
    TH  = "th"
    FR  = "fr"
    SA  = "sa"
    SU  = "su"
    ALL = "all"

    @classmethod
    def weekdays(cls) -> list["DayOfWeek"]:
        """Return Monday–Friday."""
        return [cls.MO, cls.TU, cls.WE, cls.TH, cls.FR]

    @classmethod
    def all_days(cls) -> list["DayOfWeek"]:
        """Return all seven days."""
        return [cls.MO, cls.TU, cls.WE, cls.TH, cls.FR, cls.SA, cls.SU]


# ---------------------------------------------------------------------------
# FTP job sub-types
# ---------------------------------------------------------------------------

class FtpType(str, Enum):
    """Direction of an FTP job transfer."""
    GET  = "GET"
    PUT  = "PUT"
    DEL  = "DEL"    # delete remote file


# ---------------------------------------------------------------------------
# Notification types
# ---------------------------------------------------------------------------

class NotificationType(str, Enum):
    """How AutoSys delivers job notifications."""
    EMAIL   = "EMAIL"
    SNMP    = "SNMP"
    NSM     = "NSM"
    REMEDY  = "REMEDY"


# ---------------------------------------------------------------------------
# Machine / Agent status
# ---------------------------------------------------------------------------

class MachineStatus(str, Enum):
    """Liveness state of a System Agent (machine)."""
    UP      = "UP"
    DOWN    = "DOWN"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Event source  (who put the event on the queue)
# ---------------------------------------------------------------------------

class EventSource(str, Enum):
    """Records which component raised an event — useful for audit logs."""
    SCHEDULER = "scheduler"   # ACE raised it (time trigger / dependency)
    CLI       = "cli"          # operator used sendevent / jil command
    API       = "api"          # REST API call
    AGENT     = "agent"        # System Agent reported completion
    INTERNAL  = "internal"     # internal retry / alarm logic
