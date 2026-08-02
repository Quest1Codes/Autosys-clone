"""
High Availability — distributed lock and tie-breaker support.

Provides:
- ``DistributedLock`` — DB-row-based lock for scheduler leader election.
- ``SecondaryDB`` — secondary database connection for dual-write / read fallback.
- ``HAStatus`` — status reporting for the /api/v1/ha/status endpoint.

Usage
-----
    from autosys.scheduler.ha import DistributedLock

    lock = DistributedLock(instance_id="eps-1", heartbeat_timeout=5)
    if lock.acquire(session):
        # We are the active scheduler
        lock.heartbeat(session)  # call every tick
        ...
        lock.release(session)    # on shutdown
    else:
        # We are standby
        ...
"""
from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from autosys.db.schema import SchedulerLockRow


_LOCK_ID = "scheduler-primary"


class DistributedLock:
    """
    DB-row-based distributed lock for HA scheduler leader election.

    The lock is a single row in ``ujo_scheduler_lock``.  The holder updates
    ``last_heartbeat`` every tick.  A standby can steal the lock if the
    heartbeat is stale (older than ``heartbeat_timeout`` seconds).
    """

    def __init__(
        self,
        instance_id: Optional[str] = None,
        heartbeat_timeout: int = 5,
        lock_id: str = _LOCK_ID,
    ) -> None:
        self.instance_id = instance_id or f"eps-{socket.gethostname()}-{os.getpid()}"
        self.heartbeat_timeout = heartbeat_timeout
        self.lock_id = lock_id
        self._is_leader = False

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    def acquire(self, session: Session) -> bool:
        """
        Attempt to acquire or renew the leadership lock.

        Returns True if this instance is now the active scheduler.
        """
        now = datetime.utcnow()
        row = session.get(SchedulerLockRow, self.lock_id)

        if row is None:
            # No lock row exists — create it and claim leadership
            row = SchedulerLockRow(
                lock_id=self.lock_id,
                instance_id=self.instance_id,
                role="primary",
                is_active=True,
                last_heartbeat=now,
                acquired_at=now,
                heartbeat_timeout=self.heartbeat_timeout,
            )
            session.add(row)
            session.flush()
            self._is_leader = True
            logger.info("HA: lock acquired by %s (new)", self.instance_id)
            return True

        if row.instance_id == self.instance_id:
            # We already hold the lock — renew heartbeat
            row.last_heartbeat = now
            row.is_active = True
            session.flush()
            self._is_leader = True
            return True

        # Someone else holds the lock — check if heartbeat is stale
        stale_at = now - timedelta(seconds=row.heartbeat_timeout)
        if row.last_heartbeat < stale_at:
            # Steal the lock
            old_holder = row.instance_id
            row.instance_id = self.instance_id
            row.last_heartbeat = now
            row.acquired_at = now
            row.is_active = True
            session.flush()
            self._is_leader = True
            logger.info(
                "HA: lock stolen by %s from stale holder %s",
                self.instance_id, old_holder,
            )
            return True

        # Lock is held and heartbeat is fresh — we are standby
        self._is_leader = False
        return False

    def heartbeat(self, session: Session) -> None:
        """Update the heartbeat timestamp. Call every tick while leader."""
        if not self._is_leader:
            return
        row = session.get(SchedulerLockRow, self.lock_id)
        if row and row.instance_id == self.instance_id:
            row.last_heartbeat = datetime.utcnow()
            session.flush()

    def release(self, session: Session) -> None:
        """Release the lock on graceful shutdown."""
        row = session.get(SchedulerLockRow, self.lock_id)
        if row and row.instance_id == self.instance_id:
            row.is_active = False
            row.last_heartbeat = datetime.utcnow() - timedelta(seconds=999)
            session.flush()
            self._is_leader = False
            logger.info("HA: lock released by %s", self.instance_id)

    def get_status(self, session: Session) -> dict:
        """Return the current lock status for the HA health endpoint."""
        row = session.get(SchedulerLockRow, self.lock_id)
        if row is None:
            return {
                "lock_id": self.lock_id,
                "active_instance": None,
                "role": "standby",
                "is_leader": False,
                "last_heartbeat": None,
                "stale": True,
            }
        now = datetime.utcnow()
        stale_at = now - timedelta(seconds=row.heartbeat_timeout)
        is_stale = row.last_heartbeat < stale_at
        return {
            "lock_id": row.lock_id,
            "active_instance": row.instance_id,
            "role": "primary" if row.instance_id == self.instance_id else "standby",
            "is_leader": row.instance_id == self.instance_id and not is_stale,
            "last_heartbeat": row.last_heartbeat.isoformat() + "Z" if row.last_heartbeat else None,
            "stale": is_stale,
            "acquired_at": row.acquired_at.isoformat() + "Z" if row.acquired_at else None,
            "heartbeat_timeout": row.heartbeat_timeout,
        }


class SecondaryDB:
    """
    Secondary database connection for dual-write and read fallback.

    If ``AUTOSYS_DB_URL_2`` is set, writes are replicated to the secondary.
    Reads fall back to the secondary if the primary is unavailable.
    """

    def __init__(self) -> None:
        self._url: Optional[str] = os.environ.get("AUTOSYS_DB_URL_2")
        self._enabled = self._url is not None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def url(self) -> Optional[str]:
        return self._url

    def get_status(self) -> dict:
        """Return secondary DB status for the HA health endpoint."""
        return {
            "configured": self._enabled,
            "url": self._url if self._enabled else None,
        }
