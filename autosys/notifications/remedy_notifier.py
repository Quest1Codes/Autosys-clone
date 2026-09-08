"""
Remedy Notifier — creates BMC Remedy ITSM tickets via REST API.

Configuration (env vars)
------------------------
AUTOSYS_REMEDY_API_URL    Base URL of the Remedy REST API
AUTOSYS_REMEDY_API_TOKEN  Authentication token (Bearer)
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from autosys.db.schema import AlarmRow


class RemedyNotifier:
    """
    Creates Remedy ITSM tickets via the REST API.

    Parameters
    ----------
    api_url:
        Base URL of the Remedy REST API (e.g. "https://remedy.example.com/api/arsys/v1").
    api_token:
        Bearer token for authentication.
    http_post_fn:
        Injectable for tests — replaces urllib calls.
        Signature: ``(url: str, payload: dict, headers: dict) -> bool``
    """

    def __init__(
        self,
        api_url: str,
        api_token: str,
        http_post_fn: Optional[callable] = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self._http_post = http_post_fn or _default_remedy_post

    def send(self, alarm: AlarmRow) -> bool:
        """
        Create a Remedy ticket for *alarm*.

        Returns True on successful ticket creation (2xx response).
        """
        payload = self._build_ticket(alarm)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_token}",
        }
        try:
            ok = self._http_post(
                f"{self.api_url}/entry/HPD:IncidentInterface_Create",
                payload,
                headers,
            )
            if ok:
                logger.debug(
                    "RemedyNotifier: ticket created for %r", alarm.alarm_id[:8],
                )
            return ok
        except Exception as exc:
            logger.warning("RemedyNotifier: error for %r: %s", alarm.alarm_id[:8], exc)
            return False

    @staticmethod
    def _build_ticket(alarm: AlarmRow) -> dict:
        """Build the Remedy ticket payload from an alarm."""
        return {
            "values": {
                "Summary": f"[AutoSys] {alarm.alarm_type}: {alarm.job_name}",
                "Description": alarm.message,
                "First_Name": "AutoSys",
                "Last_Name": "Scheduler",
                "Impact": "3-Moderate",
                "Urgency": "3-Medium",
                "Status": "New",
                "Reported Source": "AutoSys",
                "Service_Type": "Infrastructure Event",
                "z1D_Action": "CREATE",
                "Custom_Field_1": alarm.alarm_id,
                "Custom_Field_2": alarm.alarm_type,
                "Custom_Field_3": alarm.job_name,
            }
        }


def _default_remedy_post(url: str, payload: dict, headers: dict) -> bool:
    """POST a JSON payload to the Remedy REST API."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        logger.debug("Remedy POST %s → %d", url, exc.code)
        return False
    except Exception as exc:
        logger.debug("Remedy POST %s → error: %s", url, exc)
        return False
