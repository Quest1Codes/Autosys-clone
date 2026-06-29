"""
MachineDef — Pydantic model for a JIL ``insert_machine:`` definition.

In real AutoSys, machines can be defined three ways:
  1. GUI (Workload Automation DE)
  2. JIL file via ``insert_machine:`` stanza
  3. ``autosys machine register`` CLI (our Phase 6 addition)

This model covers path 2.  The JIL parser creates a ``MachineDef`` for
each ``insert_machine:`` stanza it encounters; the importer then upserts
a ``MachineRow`` in the ``machines`` table via ``MachineRepository``.

Real AutoSys ``insert_machine`` attributes
------------------------------------------
::

    insert_machine: etl-server-01
        type: a              # "a" = UNIX agent (only type we support)
        port: 7520
        max_load: 100        # max concurrent jobs (not enforced in Phase 7)
        description: ETL worker

We support a subset: ``type``, ``port``, ``max_load``, ``description``.
Unknown attributes are silently ignored (forward-compatibility).

The ``host`` attribute is NOT part of real AutoSys JIL (the machine name
IS the hostname in most installations).  We allow it as an extension so
you can separate the logical name from the IP:

    insert_machine: etl-server-01
        host: 192.168.1.10   # optional; defaults to machine_name
        port: 7520
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class MachineDef(BaseModel):
    """
    JIL machine definition — maps to a row in the ``machines`` table.

    Attributes
    ----------
    machine_name:
        Logical name of the machine (the key used in ``machine:`` job attrs).
    type:
        Agent type.  ``"a"`` = UNIX/Linux agent (the only type we support).
        Real AutoSys also has ``"n"`` (Windows NT) and ``"m"`` (mainframe).
    host:
        IP or hostname of the agent server.  Defaults to ``machine_name``
        if omitted (works when the logical name IS the DNS hostname).
    port:
        TCP port the System Agent listens on.  Default 7520 (same as
        real AutoSys).
    max_load:
        Maximum number of concurrent jobs on this machine.  Real AutoSys
        enforces this at the Scheduler level — we store it but don't
        enforce it until Phase 8.
    description:
        Free-text description stored in the DB.
    """

    machine_name: str
    type:         str          = "a"
    host:         Optional[str] = None
    port:         int          = Field(default=7520, ge=1, le=65535)
    max_load:     int          = Field(default=100, ge=1)
    description:  Optional[str] = None

    @model_validator(mode="after")
    def _default_host(self) -> "MachineDef":
        """If host is omitted, use the machine_name as the hostname."""
        if not self.host:
            self.host = self.machine_name
        return self
