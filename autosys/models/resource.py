"""
Pydantic model for AutoSys virtual resources.

In real AutoSys, virtual resources are a job-concurrency control mechanism.
A virtual resource has a finite number of capacity units called its ``max_load``.
Each job definition declares how many units it needs while executing via the
``job_load`` JIL attribute.  The Scheduler ACE will not dispatch a job if
doing so would cause the resource's total consumed units to exceed ``max_load``.
Instead the job enters ``QUE_WAIT`` state and is re-evaluated each time a
running job finishes and frees some units.

Mental model: ``max_load`` is a counting semaphore's initial count.  Each
running job decrements the semaphore by its ``job_load`` value.  A job that
would drive the count below zero waits in QUE_WAIT until enough jobs complete
to bring the available units back up.

Real AutoSys CLI reference
--------------------------
  caresource -A -r etl_resource -l 10   # create resource with max_load=10
  caresource -U -r etl_resource -l 20   # update max_load to 20
  caresource -D -r etl_resource         # delete resource
  caresource -s                         # list all resources
  autorep    -R etl_resource            # show current load and job queue

JIL job attributes that interact with virtual resources
-------------------------------------------------------
  ``job_load: <n>``   — units consumed by this job while RUNNING (default 1)
  ``max_load: <n>``   — the max_load cap, also writable at the job level in
                        real AutoSys as a shorthand for a per-machine resource

Common operational use cases
----------------------------
* Limiting concurrent database-loading jobs to prevent overwhelming a target DB
  with simultaneous bulk-insert sessions.
* Capping the number of simultaneous FTP transfers on a shared WAN link to avoid
  saturating bandwidth.
* Serialising jobs that write to a shared NFS output directory to prevent file
  corruption caused by concurrent writers.
* Reserving compute capacity on a Hadoop edge node (a job_load=4 job for a heavy
  MapReduce submission, with max_load=8, allowing at most two heavy jobs at once).

This clone manages virtual resources in the ``virtual_resources`` database table.
The ``current_load`` field is tracked in memory by the ``resource_manager`` service
(Phase 8) and periodically checkpointed to the database.  When a job transitions
from STARTING → RUNNING the resource_manager atomically increments
``current_load`` by the job's ``job_load``.  When a job transitions to any
terminal state (SUCCESS, FAILURE, TERMINATED) the resource_manager decrements
``current_load`` and wakes the QUE_WAIT evaluator loop so that waiting jobs can
be reconsidered.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class VirtualResource(BaseModel):
    """
    A named virtual resource used for job concurrency control.

    ``VirtualResource`` instances live in the ``virtual_resources`` table.
    Job definitions reference a resource by name via the ``job_load`` / machine
    pairing (real AutoSys) or by a direct ``resource_name`` attribute (this
    clone's extended JIL).

    The ``resource_manager`` service (Phase 8) is the sole writer of
    ``current_load``.  All other services (the Scheduler ACE, the REST API,
    the dashboard) treat ``VirtualResource`` as read-only and use
    ``can_accept()`` / ``is_saturated`` to make dispatch or display decisions.

    Mapping to real AutoSys
    -----------------------
    Real AutoSys ``caresource`` records contain the resource name and
    ``max_load``.  The current usage (analogous to ``current_load`` here) is
    computed dynamically by the ACE from the count of RUNNING jobs that
    reference the resource multiplied by their respective ``job_load`` values.
    This clone materialises ``current_load`` as an explicit field for two
    reasons:

    1. **Performance** — avoids a full table-scan of running jobs every time
       the Scheduler needs to decide whether a QUE_WAIT job can be promoted.
    2. **Simplicity** — the QUE_WAIT release logic can compare a single
       integer rather than re-aggregating job records.

    The trade-off is that ``current_load`` must be kept in sync with the
    actual set of running jobs.  The ``resource_manager`` achieves this via
    atomic increment/decrement operations wrapped in database transactions.
    """

    model_config = {"use_enum_values": True}

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    resource_name: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_.:-]+$",
        description=(
            "Unique name that identifies this virtual resource in the database "
            "and in job definitions.  This is the value a job references when "
            "declaring resource consumption.\n"
            "\n"
            "Naming conventions seen in real AutoSys deployments:\n"
            "  'etl_db_slots'      — limits concurrent database-loading jobs\n"
            "  'ftp.prod.link'     — limits concurrent FTP transfers on the\n"
            "                        production WAN link\n"
            "  'report:generator'  — limits concurrent report-generation jobs\n"
            "  'hadoop.edge01'     — limits concurrent heavy MapReduce submissions\n"
            "  'SFTP_PROD'         — all-caps style used in some AutoSys shops\n"
            "\n"
            "Allowed characters: letters (A–Z, a–z), digits (0–9), underscores "
            "(_), dots (.), colons (:), and hyphens (-).  This mirrors the "
            "naming constraints used by real AutoSys's ``caresource`` utility. "
            "Spaces and other punctuation are not permitted."
        ),
    )

    # ------------------------------------------------------------------
    # Capacity
    # ------------------------------------------------------------------

    max_load: int = Field(
        ...,
        ge=1,
        description=(
            "Maximum total ``job_load`` units that may be simultaneously "
            "consumed by RUNNING jobs.  Think of this as the total number of "
            "'slots' available on this resource.\n"
            "\n"
            "Real AutoSys command that sets this value:\n"
            "  caresource -A -r <resource_name> -l <max_load>\n"
            "\n"
            "Worked example:\n"
            "  max_load = 10\n"
            "  Job A is running with job_load = 4   → current_load = 4\n"
            "  Job B is running with job_load = 4   → current_load = 8\n"
            "  Job C wants to start with job_load = 3:\n"
            "    available_slots = 10 - 8 = 2  <  3  → Job C enters QUE_WAIT\n"
            "  Job A finishes → current_load = 4\n"
            "    available_slots = 10 - 4 = 6  ≥  3  → Job C is dispatched\n"
            "\n"
            "A resource with ``max_load=1`` acts as a binary mutex: at most "
            "one job with ``job_load=1`` can run at a time, effectively "
            "serialising all jobs that consume this resource.\n"
            "\n"
            "Must be >= 1 (a resource with zero capacity would permanently "
            "block all jobs that reference it, which is never useful)."
        ),
    )

    current_load: int = Field(
        0,
        ge=0,
        description=(
            "Running sum of ``job_load`` values for all currently RUNNING jobs "
            "that are consuming this resource.  This field is maintained "
            "exclusively by the ``resource_manager`` service and must not be "
            "modified by any other component.\n"
            "\n"
            "Life-cycle of ``current_load``:\n"
            "\n"
            "  Increment — when a job transitions STARTING → RUNNING:\n"
            "    resource_manager.acquire(resource_name, job.job_load)\n"
            "    # current_load += job.job_load\n"
            "\n"
            "  Decrement — when a job transitions to any terminal state\n"
            "    (SUCCESS, FAILURE, TERMINATED):\n"
            "    resource_manager.release(resource_name, job.job_load)\n"
            "    # current_load -= job.job_load\n"
            "    # Then wake the QUE_WAIT evaluator loop.\n"
            "\n"
            "This field should never exceed ``max_load`` under correct "
            "operation because the ``resource_manager`` calls ``can_accept()`` "
            "before each acquire.  It should never go below 0; if it does, "
            "the ``resource_manager`` logs an error and clamps to 0.\n"
            "\n"
            "Default 0 means no jobs are currently consuming this resource."
        ),
    )

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    description: Optional[str] = Field(
        None,
        description=(
            "Free-text explanation of what this resource represents, which "
            "jobs consume it, and any operational notes.\n"
            "\n"
            "Example:\n"
            "  'Limits concurrent bulk-insert sessions into the DW Oracle "
            "database.  max_load=10 matches the DB's configured max_sessions "
            "for the ETL user.  Heavy jobs (full-load) use job_load=4; "
            "incremental jobs use job_load=1.'\n"
            "\n"
            "Not used by the Scheduler or any runtime component."
        ),
    )

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description=(
            "UTC timestamp when this resource record was first created in the "
            "``virtual_resources`` table.  Set automatically on insertion by "
            "the ``resource_manager``; never modified afterwards.  Useful for "
            "auditing when a resource was introduced and by whom."
        ),
    )

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def available_slots(self) -> int:
        """Return the number of ``job_load`` units currently free on this resource.

        Calculated as ``max_load - current_load``.  This is the maximum
        ``job_load`` value a single new job can declare and still be dispatched
        immediately without entering QUE_WAIT.

        A value of 0 means the resource is fully saturated.  A negative value
        should never occur under correct operation but could theoretically arise
        during crash-recovery if ``current_load`` was not properly decremented
        before a process restart.  The ``resource_manager`` monitors for and
        corrects negative values.

        The resource_manager's QUE_WAIT promotion loop uses this property to
        iterate over waiting jobs in priority order and promote any whose
        ``job_load <= available_slots``.

        Returns
        -------
        int
            ``self.max_load - self.current_load``
        """
        return self.max_load - self.current_load

    @property
    def is_saturated(self) -> bool:
        """Return ``True`` when no additional ``job_load`` units can be accommodated.

        A resource is saturated when ``current_load >= max_load``, meaning
        every unit of capacity is already consumed by running jobs.  Any job
        that declares a ``job_load >= 1`` must enter QUE_WAIT and wait for a
        running job to finish.

        In real AutoSys, a saturated resource causes the Scheduler to place the
        next eligible job into QUE_WAIT state.  The Scheduler re-evaluates
        resource availability each time a running job transitions to a terminal
        state (SUCCESS, FAILURE, TERMINATED) and frees its ``job_load`` units.

        This property is a convenience alias for ``self.available_slots <= 0``
        and is used in the Scheduler's fast-path dispatch check::

            if resource.is_saturated:
                transition_job(job, JobStatus.QUE_WAIT)
                return

        Returns
        -------
        bool
            ``True`` if ``self.current_load >= self.max_load`` (no free slots).
            ``False`` if at least one unit of capacity is available.
        """
        return self.current_load >= self.max_load

    # ------------------------------------------------------------------
    # Instance methods
    # ------------------------------------------------------------------

    def can_accept(self, job_load: int) -> bool:
        """Return ``True`` when this resource has sufficient free capacity for a job.

        This is the primary dispatch gate used by the Scheduler ACE and the
        ``resource_manager`` before promoting a WAIT_REPLY or QUE_WAIT job to
        STARTING.  It encapsulates the single condition:

            available_slots  >=  job_load

        If this returns ``True``, the ``resource_manager`` may atomically
        increment ``current_load`` by ``job_load`` and allow the Scheduler to
        transition the job to STARTING.  If it returns ``False``, the job must
        remain in QUE_WAIT and be retried after the next release event.

        Important: this method is a **pure query** — it reads ``current_load``
        and ``max_load`` but does NOT modify any state.  The decision to
        actually acquire the resource and increment ``current_load`` is made
        by the ``resource_manager`` inside a database transaction to ensure
        atomicity (preventing two concurrent dispatch decisions from both
        observing ``can_accept=True`` and double-booking the last available
        slot).

        Parameters
        ----------
        job_load:
            The ``job_load`` value declared on the job being considered for
            dispatch.  Corresponds to ``Job.job_load`` in the ``jobs`` table.
            Must be >= 1; a value of 0 would allow any number of jobs to run
            simultaneously regardless of the resource's ``max_load``.

        Returns
        -------
        bool
            ``True`` if ``self.available_slots >= job_load``, meaning the job
            can be dispatched immediately without violating ``max_load``.
            ``False`` if the job must wait in QUE_WAIT state.

        Examples
        --------
        >>> r = VirtualResource(resource_name="etl_db_slots", max_load=10, current_load=8)
        >>> r.can_accept(job_load=1)
        True   # 2 free slots, need 1 — OK
        >>> r.can_accept(job_load=3)
        False  # 2 free slots, need 3 — QUE_WAIT
        >>> r.can_accept(job_load=2)
        True   # 2 free slots, need 2 — exactly enough
        """
        return self.available_slots >= job_load
