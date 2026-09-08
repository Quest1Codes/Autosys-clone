"""Cloud & Airflow integration — submit jobs to Airflow, AWS, GCP."""
from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger


class AirflowClient:
    """
    Submit jobs to Apache Airflow via its REST API.

    Parameters
    ----------
    base_url:
        Airflow API base URL (e.g. "http://airflow:8080/api/v1")
    username, password:
        Basic auth credentials.
    http_post_fn:
        Injectable for tests. Signature: (url, headers, body) -> dict
    """

    def __init__(
        self,
        base_url: str,
        username: str = "admin",
        password: str = "admin",
        http_post_fn: Optional[callable] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._http_post = http_post_fn or _default_http_post

    def trigger_dag(self, dag_id: str, conf: Optional[dict] = None) -> dict:
        """Trigger an Airflow DAG run."""
        url = f"{self.base_url}/dags/{dag_id}/dagRuns"
        body = json.dumps({"conf": conf or {}})
        headers = {"Content-Type": "application/json"}
        return self._http_post(url, headers, body)


def _default_http_post(url: str, headers: dict, body: str) -> dict:
    """Default HTTP POST using urllib."""
    import urllib.request
    req = urllib.request.Request(url, data=body.encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


class CloudJobSubmitter:
    """
    Submit jobs to cloud platforms (AWS, GCP, Azure).

    Uses connection profiles from the database for credentials and config.
    """

    def __init__(self, http_post_fn: Optional[callable] = None) -> None:
        self._http_post = http_post_fn or _default_http_post

    def submit_aws_batch(
        self,
        job_definition: str,
        job_queue: str,
        command: list[str],
        region: str = "us-east-1",
    ) -> dict:
        """Submit a job to AWS Batch."""
        url = f"https://batch.{region}.amazonaws.com/v1/submitjob"
        body = json.dumps({
            "jobDefinition": job_definition,
            "jobQueue": job_queue,
            "containerOverrides": {"command": command},
        })
        return self._http_post(url, {"Content-Type": "application/json"}, body)

    def submit_gcp_cloudrun(
        self,
        service_url: str,
        command: str,
    ) -> dict:
        """Submit a job to GCP Cloud Run."""
        body = json.dumps({"command": command})
        return self._http_post(service_url, {"Content-Type": "application/json"}, body)
