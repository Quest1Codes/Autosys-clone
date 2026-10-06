"""
Shared helpers for *applying* parsed JIL operations to the database.

Used by both the ``autosys jil import`` CLI and the ``/api/v1/jil/import``
endpoint so the two behave identically:

* :func:`apply_job_update`       -- ``update_job`` / ``override_job`` MERGE the
  attributes the stanza names into the existing definition (untouched
  attributes keep their values) instead of overwriting them with defaults.
* :func:`apply_null_attrs`       -- ``attr: NULL`` clears an attribute.
* :func:`delete_job_keep_children` -- ``delete_job`` on a BOX removes only the
  box; its jobs become stand-alone (vendor PDF, ``delete_job``).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from autosys.db.schema import JobRow
from autosys.models.job import Job, parse_job
from autosys.parser.jil_parser import JILOperation

# Runtime state — never part of a JIL definition, never merged.
_RUNTIME_KEYS = frozenset({
    "status", "last_start", "last_end", "last_run_date", "created_at", "updated_at",
})


def _known_fields(job: Job) -> set[str]:
    return set(Job.model_fields) | set(type(job).model_fields)


def apply_null_attrs(session: Session, job_name: str, attrs: list[str]) -> list[str]:
    """
    Clear *attrs* on job *job_name* (``attr: NULL``).  Nullable columns become
    NULL; NOT NULL columns return to their default; attributes stored in
    ``extra_attrs_json`` are removed.  Returns the attributes actually cleared.
    """
    if not attrs:
        return []
    row: Optional[JobRow] = session.get(JobRow, job_name)
    if row is None:
        return []
    cleared: list[str] = []
    columns = JobRow.__table__.columns
    for attr in attrs:
        col = columns.get(attr) if attr not in _RUNTIME_KEYS else None
        if col is not None and not col.primary_key and attr != "job_type":
            if col.nullable:
                setattr(row, attr, None)
            elif col.default is not None and getattr(col.default, "is_scalar", False):
                setattr(row, attr, col.default.arg)
            else:
                continue
            cleared.append(attr)
            continue
        raw = getattr(row, "extra_attrs_json", None)
        if raw:
            try:
                extras = json.loads(raw)
            except ValueError:
                continue
            hit = [k for k in extras if k.lower() == attr.lower()]
            if hit:
                for k in hit:
                    del extras[k]
                row.extra_attrs_json = json.dumps(extras) if extras else None
                cleared.append(attr)
    return cleared


def apply_job_update(session: Session, op: JILOperation,
                     issues: Optional[list] = None) -> tuple[str, str]:
    """
    Apply an ``update`` / ``override`` operation by merging into the existing
    definition.  Returns ``(action, job_type)``.
    """
    from autosys.db.repository import jobs as job_repo

    new: dict[str, Any] = dict(op.coerced)
    name = new["job_name"]
    existing = job_repo.get(session, name)

    if existing is None:
        # Nothing to merge into.  A stanza that is a complete definition on
        # its own is inserted; a bare fragment cannot be.
        if "job_type" in new:
            try:
                job = parse_job(new)
            except Exception:
                return "SKIPPED", "job not found"
            job_repo.upsert(session, job)
            return "INSERTED", str(job.job_type)
        return "SKIPPED", "job not found"

    merged: dict[str, Any] = {
        k: v for k, v in existing.model_dump(exclude_none=True).items()
        if k not in _RUNTIME_KEYS
    }
    known = _known_fields(existing)
    extras = dict(merged.pop("extra_attrs", None) or {})
    for key, value in new.items():
        if key in known:
            merged[key] = value
        else:
            extras[key] = value
    for attr in op.null_attrs:
        merged.pop(attr, None)
        for k in [k for k in extras if k.lower() == attr.lower()]:
            del extras[k]
    if extras:
        merged["extra_attrs"] = extras

    try:
        job = parse_job(merged)
    except Exception:
        if issues is None:
            raise
        from autosys.models.job import parse_job_lenient
        job, warnings = parse_job_lenient(merged)
        for w in warnings:
            issues.append({"severity": "warning", "code": "job_validation", "line": op.start_line,
                           "message": w})
    job_repo.upsert(session, job)
    session.flush()
    apply_null_attrs(session, name, op.null_attrs)
    return "UPDATED", str(job.job_type)


def delete_job_keep_children(session: Session, job_name: str) -> int:
    """
    ``delete_job``: remove the job.  If it is a BOX its child jobs are kept and
    become stand-alone (``box_name`` cleared).  Returns the number of jobs
    that were detached.
    """
    from autosys.db.repository import jobs as job_repo

    row: Optional[JobRow] = session.get(JobRow, job_name)
    if row is None:
        return 0
    children = session.scalars(select(JobRow).where(JobRow.box_name == job_name)).all()
    for child in children:
        child.box_name = None
    session.flush()
    job_repo.delete(session, job_name)
    return len(children)


# ===========================================================================
# apply_operation — persist ONE parsed operation, never raising for content
# ===========================================================================

from dataclasses import dataclass, field  # noqa: E402


@dataclass
class ApplyResult:
    """Outcome of applying one operation."""
    action:    str                       # INSERTED / UPDATED / DELETED / MACHINE / SKIPPED ...
    name:      str
    detail:    str = ""
    counter:   str = ""                  # which summary counter to bump
    count:     int = 1                   # deleted-job count for delete_box
    persisted: bool = True               # typed data stored in a typed table
    issues:    list = field(default_factory=list)


def _int(value, default: int, issues: list, what: str, line: int) -> int:
    """int(value) that reports instead of raising."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        issues.append({"severity": "warning", "code": "invalid_value", "line": line,
                       "message": f"{what}={value!r} is not an integer; used {default} "
                                  "(original text kept in the raw source)"})
        return default


# Sub-commands the parser accepts but that have no table in this simulator.
UNSTORED_OPS = frozenset({
    "insert_view", "modify_view", "delete_view",
    "insert_filter", "modify_filter", "delete_filter",
    "insert_alert_policy", "modify_alert_policy", "delete_alert_policy",
    "delete_user",
})


BLOB_FILE_MAX_BYTES = 16 * 1024 * 1024


def _read_capped(path: str) -> str:
    """Read a ``blob_file`` for the local CLI, refusing anything over
    BLOB_FILE_MAX_BYTES (``/dev/zero`` or a huge file would exhaust memory)."""
    with open(path, errors="replace") as fh:
        data = fh.read(BLOB_FILE_MAX_BYTES + 1)
    if len(data) > BLOB_FILE_MAX_BYTES:
        raise OSError(f"larger than {BLOB_FILE_MAX_BYTES} bytes")
    return data


def apply_operation(
    session: Session,
    op: JILOperation,
    dry_run: bool = False,
    read_files: bool = False,
) -> ApplyResult:
    """
    Persist one parsed operation and describe what happened.

    Content problems never raise: they become ``issues`` on the result and the
    operation is reported as not (fully) persisted, so the caller can archive
    its raw text.  ``blob_file`` / glob file references are opened only with
    ``read_files=True`` -- the operator's own `jil import FILE` -- never from
    the API, where the path would name a file on the server (audit SEC-05).
    """
    from autosys.db.repository import (
        jobs as job_repo, machines as machine_repo, resources as resource_repo,
        job_types as job_type_repo, monitors as monitor_repo, blobs as blob_repo,
        globs2 as glob_repo, xinsts as xinst_repo, profiles as profile_repo,
        calendars as cal_repo,
    )
    import json as _json

    o      = op.op
    attrs  = op.raw_attrs
    issues: list = []
    line   = op.source_line
    R = lambda *a, **k: ApplyResult(*a, issues=issues, **k)   # noqa: E731

    # ---- machines -------------------------------------------------------
    if o in ("insert_machine", "update_machine"):
        m = op.machine
        if not dry_run:
            members_json = _json.dumps(m.members) if getattr(m, "members", None) else None
            kwargs = dict(machine_name=m.machine_name, host=m.host or m.machine_name, port=m.port)
            if members_json is not None:
                kwargs["members_json"] = members_json
            machine_repo.register(session, **kwargs)
        return R("MACHINE", m.machine_name, f"port:{m.port}" if o == "insert_machine" else "updated",
                 counter="machines")
    if o == "delete_machine":
        name = attrs.get("machine_name", "")
        if not dry_run:
            machine_repo.delete(session, name)
        return R("DELETED", name, "machine", counter="machines")

    # ---- job operations ---------------------------------------------------
    if o == "rename":
        old, new = attrs.get("job_name", ""), attrs.get("new_name", "")
        if not new:
            issues.append({"severity": "warning", "code": "rename_no_target", "line": line,
                           "message": "rename_job without new_name; nothing renamed"})
            return R("SKIPPED", old, "rename without new_name", persisted=False)
        if not dry_run:
            job_repo.rename(session, old, new)
        return R("RENAMED", old, f"→ {new}")
    if o == "delete_box":
        name = op.job.job_name
        count = 0 if dry_run else job_repo.delete_box(session, name)
        return R("DELETED", name, f"box ({count} jobs)", counter="deleted", count=count)
    if o in ("override", "update"):
        name = op.job.job_name
        if dry_run:
            action, jtype = "OK", ("override" if o == "override" else str(op.job.job_type))
            return R(action, name, jtype)
        action, jtype = apply_job_update(session, op, issues)
        if o == "override":
            jtype = "override"
        counter = {"UPDATED": "updated", "INSERTED": "inserted"}.get(action, "")
        return R(action, name, jtype, counter=counter, persisted=action != "SKIPPED")
    if o == "override_delete":
        # Overrides are applied in place (there is no separate override store),
        # so there is nothing to cancel.
        return R("SKIPPED", op.job.job_name, "override delete (no stored override)", persisted=False)
    if o == "delete":
        name = op.job.job_name
        if not dry_run:
            delete_job_keep_children(session, name)
        return R("DELETED", name, str(op.job.job_type), counter="deleted")
    if o == "insert":
        job = op.job
        if dry_run:
            return R("OK", job.job_name, str(job.job_type))
        result = job_repo.upsert(session, job)
        action = result.upper()
        return R(action, job.job_name, str(job.job_type),
                 counter="inserted" if action == "INSERTED" else "updated")

    # ---- resources ----------------------------------------------------------
    if o in ("insert_resource", "update_resource"):
        name = attrs.get("resource_name", "")
        max_load = _int(attrs.get("max_load", "1"), 1, issues, "max_load", line)
        if not dry_run:
            resource_repo.upsert(session, name, max_load=max_load, description=attrs.get("description"))
        return R("RESOURCE", name, f"max_load:{max_load}", counter="resources")
    if o == "delete_resource":
        name = attrs.get("resource_name", "")
        if not dry_run:
            resource_repo.delete(session, name)
        return R("DELETED", name, "resource", counter="resources")

    # ---- job types ------------------------------------------------------------
    if o in ("insert_job_type", "update_job_type"):
        name = attrs.get("job_type_name", "")
        desc = attrs.get("description")
        if not dry_run:
            job_type_repo.upsert(session, name, command_template=attrs.get("command"), description=desc)
        return R("JOB_TYPE", name, desc or "", counter="job_types")
    if o == "delete_job_type":
        name = attrs.get("job_type_name", "")
        if not dry_run:
            job_type_repo.delete(session, name)
        return R("DELETED", name, "job_type", counter="job_types")

    # ---- monbro ------------------------------------------------------------------
    if o in ("insert_monbro", "update_monbro"):
        name = attrs.get("monbro_name", "")
        mtype = attrs.get("monbro_type", "FILE_MONITOR")
        extra = {k: v for k, v in attrs.items() if k not in ("monbro_name", "monbro_type", "job_name")}
        if not dry_run:
            monitor_repo.upsert(session, name, mtype, job_name=attrs.get("job_name"),
                                attributes_json=_json.dumps(extra) if extra else None)
        return R("MONBRO", name, mtype, counter="monitors")
    if o == "delete_monbro":
        name = attrs.get("monbro_name", "")
        if not dry_run:
            monitor_repo.delete(session, name)
        return R("DELETED", name, "monbro", counter="monitors")

    # ---- blobs / globs ---------------------------------------------------------------
    if o in ("insert_blob", "update_blob"):
        name = attrs.get("blob_name", "")
        content = attrs.get("blob_input", "")
        bfile = attrs.get("blob_file", "")
        if not content and bfile:
            if read_files:
                try:
                    content = _read_capped(bfile)
                except OSError as exc:
                    issues.append({"severity": "warning", "code": "blob_file_unreadable", "line": line,
                                   "message": f"blob_file {bfile!r} could not be read ({exc})"})
            else:
                issues.append({"severity": "info", "code": "blob_file_not_read", "line": line,
                               "message": f"blob_file {bfile!r} referenced, not read"})
        if not dry_run:
            blob_repo.insert(session, name, content, job_name=attrs.get("job_name"))
        return R("BLOB", name, attrs.get("job_name") or "", counter="blobs")
    if o == "delete_blob":
        name = attrs.get("blob_name", "")
        if not dry_run:
            blob_repo.delete(session, name)
        return R("DELETED", name, "blob", counter="blobs")
    if o in ("insert_glob", "update_glob"):
        name = attrs.get("global_name", "")
        content = attrs.get("blob_input", "")
        gfile = attrs.get("blob_file", "")
        if not content and "global_value" in attrs:
            content = attrs["global_value"]
        if not content and gfile:
            if read_files:
                try:
                    content = _read_capped(gfile)
                except OSError as exc:
                    issues.append({"severity": "warning", "code": "blob_file_unreadable", "line": line,
                                   "message": f"blob_file {gfile!r} could not be read ({exc})"})
            else:
                issues.append({"severity": "info", "code": "blob_file_not_read", "line": line,
                               "message": f"blob_file {gfile!r} referenced, not read"})
        if not dry_run:
            glob_repo.upsert(session, name, content)
        return R("GLOB", name, "", counter="globs")
    if o == "delete_glob":
        name = attrs.get("global_name", "")
        if not dry_run:
            glob_repo.delete(session, name)
        return R("DELETED", name, "glob", counter="globs")

    # ---- external instances -----------------------------------------------------------------
    if o in ("insert_xinst", "update_xinst"):
        name = attrs.get("xinst_name", "")
        inst = attrs.get("instance_name", name)
        host = attrs.get("host", "localhost")
        port = _int(attrs.get("port", "9000"), 9000, issues, "port", line)
        if not dry_run:
            xinst_repo.upsert(session, name, inst, host, port=port, description=attrs.get("description"))
        return R("XINST", name, f"{host}:{port}", counter="xinsts")
    if o == "delete_xinst":
        name = attrs.get("xinst_name", "")
        if not dry_run:
            xinst_repo.delete(session, name)
        return R("DELETED", name, "xinst", counter="xinsts")

    # ---- connection profiles --------------------------------------------------------------------
    if o in ("insert_connectionprofile", "update_connectionprofile"):
        name = attrs.get("profile_name", "")
        ptype = attrs.get("profile_type", "HADOOP")
        extra = {k: v for k, v in attrs.items() if k not in ("profile_name", "profile_type")}
        if not dry_run:
            profile_repo.upsert(session, name, ptype, attributes_json=_json.dumps(extra) if extra else None)
        return R("PROFILE", name, ptype, counter="profiles")
    if o == "delete_connectionprofile":
        name = attrs.get("profile_name", "")
        if not dry_run:
            profile_repo.delete(session, name)
        return R("DELETED", name, "profile", counter="profiles")

    # ---- calendars -----------------------------------------------------------------------------------
    if o in ("insert_calendar", "update_calendar"):
        name = attrs.get("calendar_name", "")
        if not dry_run:
            from autosys.db.schema import CalendarRow
            dates_raw = attrs.get("dates", "")
            existing = session.get(CalendarRow, name)
            if dates_raw:
                dates_list = [d.strip() for d in dates_raw.split(",") if d.strip()]
            elif o == "update_calendar" and existing is not None:
                dates_list = _json.loads(existing.dates_json or "[]")   # unspecified: keep
            else:
                dates_list = []
            desc = attrs.get("description") or (existing.description if existing else None)
            cal_repo.upsert(session, CalendarRow(
                calendar_name=name, dates_json=_json.dumps(dates_list), description=desc))
        return R("CALENDAR", name, "", counter="calendars")
    if o == "delete_calendar":
        name = attrs.get("calendar_name", "")
        if not dry_run:
            cal_repo.delete(session, name)
        return R("DELETED", name, "calendar", counter="calendars")

    # ---- parsed, but no typed table -------------------------------------------------------------------
    if o in UNSTORED_OPS:
        name = next((v for k, v in attrs.items() if k.endswith("_name")), "?")
        return R("SKIPPED", name, f"{o} (parsed, not stored)", persisted=False)
    if o == "raw":
        return R("SKIPPED", op.raw_name or "?", "unparseable block (quarantined)", persisted=False)
    return R("SKIPPED", "?", f"{o} (unsupported)", persisted=False)
