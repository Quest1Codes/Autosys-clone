"""
Cross-Instance (xinst) support — remote job status lookup.

Provides:
- ``RemoteInstanceClient`` — HTTP client to query job status on a remote AutoSys instance.
- ``resolve_xinst_condition`` — resolve ``success(remote:job)`` conditions by
  fetching remote job status.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from autosys.db.schema import ExternalInstanceRow
from autosys.db.repository import xinsts as xinst_repo


class RemoteInstanceClient:
    """
    HTTP client to query job status on a remote AutoSys instance.

    Parameters
    ----------
    host:
        Remote instance host.
    port:
        Remote instance port (default 9000).
    http_get_fn:
        Injectable for tests — replaces urllib calls.
        Signature: ``(url: str) -> dict``
    """

    def __init__(
        self,
        host: str,
        port: int = 9000,
        http_get_fn: Optional[callable] = None,
    ) -> None:
        self.host = host
        self.port = port
        self._http_get = http_get_fn or _default_http_get

    def get_job_status(self, job_name: str) -> Optional[str]:
        """
        Query the remote instance for a job's current status.

        Returns the status string (e.g. "SUCCESS") or None on error.
        """
        url = f"http://{self.host}:{self.port}/api/v1/jobs/{job_name}"
        try:
            data = self._http_get(url)
            return data.get("status")
        except Exception as exc:
            logger.warning("xinst: failed to query %s: %s", url, exc)
            return None


def _default_http_get(url: str) -> dict:
    """Default HTTP GET using urllib."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        logger.debug("xinst HTTP GET %s → %d", url, exc.code)
        raise
    except Exception as exc:
        logger.debug("xinst HTTP GET %s → error: %s", url, exc)
        raise


def resolve_xinst_condition(
    session: Session,
    condition_str: str,
    local_statuses: dict[str, str],
) -> bool:
    """
    Evaluate a condition that may reference remote instance jobs.

    Syntax: ``success(remote:job_name)`` where ``remote`` is an xinst_name
    defined in the ExternalInstanceRow table.

    Parameters
    ----------
    session:
        Open SQLAlchemy session for looking up xinst definitions.
    condition_str:
        The condition string, possibly containing ``remote:job`` references.
    local_statuses:
        Local job status snapshot.

    Returns
    -------
    bool
        True if the condition is satisfied.
    """
    if not condition_str:
        from autosys.scheduler.condition_evaluator import is_satisfied
        return is_satisfied(condition_str, local_statuses)

    # Check if condition contains xinst references (func(instance:job) pattern)
    import re
    remote_pattern = re.compile(r"(success|failure|done|terminated|notrunning|s|f|d|t|n)\((\w+):(\w+)\)")
    has_xinst = bool(remote_pattern.search(condition_str))
    if not has_xinst:
        from autosys.scheduler.condition_evaluator import is_satisfied
        return is_satisfied(condition_str, local_statuses)

    remote_statuses = {}

    for match in remote_pattern.finditer(condition_str):
        func, xinst_name, job_name = match.group(1), match.group(2), match.group(3)
        if xinst_name == "remote":
            # Generic "remote" prefix — not supported, needs instance name
            continue
        # Look up the xinst definition
        xinst = xinst_repo.get(session, xinst_name)
        if xinst is None:
            logger.warning("xinst: instance %r not found", xinst_name)
            continue
        # Fetch remote job status (cache per call)
        cache_key = f"{xinst_name}:{job_name}"
        if cache_key not in remote_statuses:
            client = RemoteInstanceClient(host=xinst.host, port=xinst.port)
            status = client.get_job_status(job_name)
            remote_statuses[cache_key] = status or "INACTIVE"

    # Merge remote statuses into the snapshot with prefixed keys
    merged_statuses = dict(local_statuses)
    for cache_key, status in remote_statuses.items():
        # The condition parser looks up by job_name, so we need to
        # inject the remote job with a composite key
        parts = cache_key.split(":", 1)
        # Replace "xinst:job" with just "job" in the condition for evaluation
        # and add the remote status under the job name
        if len(parts) == 2:
            merged_statuses[parts[1]] = status

    # Rewrite condition to remove xinst prefix: success(remote:job) → success(job)
    rewritten = re.sub(r"(\w+)\(\w+:(\w+)\)", r"\1(\2)", condition_str)

    from autosys.scheduler.condition_evaluator import is_satisfied
    return is_satisfied(rewritten, merged_statuses)
