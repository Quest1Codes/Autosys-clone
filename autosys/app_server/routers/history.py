"""
History router — load a client's real AutoSys run history via REST.

POST /api/v1/history/import   Upload a run-history export (multipart) and persist it.
GET  /api/v1/history/coverage How much of the estate has observed vs simulated risk.

Unlike /api/v1/jil/import, which takes JSON ({"content": "..."}), this route
takes a multipart file upload (UploadFile): a run-history export is expected
to be large -- 86K jobs x weeks of history -- and a JSON string body would
mean buffering the whole file into one Python str before parsing starts. A
very large file (beyond AUTOSYS_HISTORY_MAX_UPLOAD_MB) should go through
`autosys history import` instead, the same way JilImportModal.tsx's own
BULK_CLI_THRESHOLD comment already steers bulk JIL imports to the CLI.
"""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import (
    HistoryCoverageResponse, HistoryImportResponse,
)
from autosys.parser.run_history_ingest import (
    _serialise_history_imports, ingest_run_history_stream,
)

router = APIRouter(prefix="/api/v1/history", tags=["history"])

# Matches nginx.conf / nginx.bundled.conf's client_max_body_size 200m by
# default -- a request nginx would already have rejected should not reach
# this far, but the check also applies when something else fronts this API
# (an Ingress with a different body-size limit, a direct connection in dev).
_MAX_UPLOAD_MB = int(os.environ.get("AUTOSYS_HISTORY_MAX_UPLOAD_MB", "200"))


def _synthetic_path(user: CurrentUser) -> str:
    who = getattr(user, "username", None) or "unknown"
    return f"api:history:{who}:{uuid.uuid4().hex}"


@router.post("/import", response_model=HistoryImportResponse)
def import_history(
    request:           Request,
    file:              UploadFile,
    declared_timezone: str | None = None,
    on_conflict:       str = "skip",
    session:           Session     = Depends(get_session),
    user:              CurrentUser = Depends(get_current_user),
) -> HistoryImportResponse:
    """
    Upload a run-history export and persist it with ``source="imported"``.

    Accepts CSV, TSV, pipe-delimited text, or JSON Lines -- whichever the
    caller's export tooling already produces; see
    autosys/parser/run_history_ingest.py's module docstring for the column
    contract. ``declared_timezone`` is an IANA name or "UTC", required only
    if the file's timestamps carry no UTC offset of their own.

    A file over :data:`_MAX_UPLOAD_MB` is refused with 413 before anything
    is read; use ``autosys history import`` for anything larger.
    """
    user.require_role("operator", "admin")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_UPLOAD_MB * 1024 * 1024:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=(f"upload exceeds {_MAX_UPLOAD_MB} MB -- use "
                            "`autosys history import` for a file this large"),
                )
        except ValueError:
            pass   # malformed header; let the actual read surface any real problem

    # Mutual exclusivity with Run All Boxes / dry-run data. The toolbar's own
    # disable-check (JobMonitor.tsx) is only a point-in-time coverage
    # snapshot and can go stale: Run All Boxes triggers the live dry-run EPS,
    # which keeps cascading through the box tree over several ticks after
    # the click returns, writing source="simulated" rows gradually in the
    # background -- not in one batch the way this endpoint's own import is.
    # A snapshot taken before that cascade finishes can read zero simulated
    # evidence even while more is actively being written, letting a client
    # that already decided to proceed land here anyway. This check is run
    # fresh, in the same request as the import itself, so it can't go stale
    # the same way.
    from autosys.analysis.operational_risk import fetch_run_stats
    from autosys.db.repository import jobs as job_repo

    job_names = [r.job_name for r in job_repo.list_all(session)]
    if job_names:
        simulated = fetch_run_stats(session, job_names, sources=("simulated",))
        if simulated:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{len(simulated)} job(s) already have simulated run history from "
                    "Run All Boxes / dry-run — importing real history now would mix "
                    "generated data with real evidence. Clear the estate (Job Monitor's "
                    "\"Clear Estate\") and re-import the JIL before importing real "
                    "history."
                ),
            )

    _serialise_history_imports(session)
    report = ingest_run_history_stream(
        session, file.file, _synthetic_path(user),
        declared_timezone=declared_timezone, on_conflict=on_conflict,
    )

    return HistoryImportResponse(
        import_id=report.import_id, status=report.status,
        format=report.format, encoding=report.encoding,
        reject_reason=report.reject_reason,
        n_rows_read=report.n_rows_read, n_rows_loaded=report.n_rows_loaded,
        n_rows_skipped=report.n_rows_skipped, n_rows_duplicate=report.n_rows_duplicate,
        n_rows_unknown_job=report.n_rows_unknown_job, n_jobs=report.n_jobs,
        window_start=report.window_start, window_end=report.window_end,
        issue_codes=report.issue_codes,
        unknown_job_samples=report.unknown_job_samples[:20],
        aborted=report.aborted, abort_reason=report.abort_reason,
    )


@router.get("/coverage", response_model=HistoryCoverageResponse)
def get_coverage(
    session: Session     = Depends(get_session),
    _user:   CurrentUser = Depends(get_current_user),
) -> HistoryCoverageResponse:
    """
    How much of the estate currently has observed (real) vs simulated
    operational risk -- the same numbers the assessment report's
    ``coverage`` block exposes, without running the full complexity scan.
    """
    from autosys.analysis.operational_risk import (
        MIN_OBSERVED_RUNS, MIN_OBSERVED_WINDOW_DAYS, fetch_run_stats,
    )
    from autosys.db.repository import jobs as job_repo

    rows = job_repo.list_all(session)
    job_names = [r.job_name for r in rows]
    imported  = fetch_run_stats(session, job_names, sources=("imported",))
    live      = fetch_run_stats(session, job_names, sources=("live",))
    # "simulated" specifically, not live+simulated: a dry-run-shipped
    # container (the only mode this ships in) never writes "live" rows going
    # forward -- _record_run always tags "simulated" (see F0 in
    # dev/task10-productionization/04-simulator-flaws.md). "live" only
    # persists as a legacy backfill default from before that fix existed, so
    # it's not a meaningful signal for "did someone trigger Run All Boxes /
    # a dry-run completion" -- "simulated" is exactly that signal.
    simulated = fetch_run_stats(session, job_names, sources=("simulated",))
    observed  = {**live, **imported}

    non_box = [r for r in rows if (r.job_type or "").upper() != "BOX"]
    sufficient = sum(1 for r in non_box if observed.get(r.job_name)
                     and observed[r.job_name].has_sufficient_sample())
    thin = sum(1 for r in non_box if observed.get(r.job_name)
              and not observed[r.job_name].has_sufficient_sample())
    no_data = len(non_box) - sufficient - thin

    return HistoryCoverageResponse(
        jobs_total=len(rows), jobs_non_box=len(non_box),
        jobs_observed_sufficient=sufficient, jobs_observed_insufficient=thin,
        jobs_no_evidence=no_data,
        coverage_pct=round(sufficient / len(non_box), 4) if non_box else 0.0,
        min_runs_threshold=MIN_OBSERVED_RUNS,
        min_window_days_threshold=MIN_OBSERVED_WINDOW_DAYS,
        jobs_with_imported_evidence=len(imported),
        jobs_with_dry_run_evidence=len(simulated),
    )
