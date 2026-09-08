"""
Dispatcher — sends alarm payloads to configured notification channels.

Channels
--------
NSM webhook     POST {alarm_id, job_name, alarm_type, message, ts}
Email (SMTP)    Plain-text message via smtplib
Slack           Incoming-webhook block-kit message
PagerDuty       Events API v2 trigger

All channels are optional.  If the relevant env-var is absent the channel
is silently skipped.  Failures on individual channels are logged but do
not prevent the other channels from being tried.

Usage
-----
    from autosys.notifications.dispatcher import Dispatcher
    from autosys.notifications.config import NotificationConfig

    cfg = NotificationConfig()
    d = Dispatcher(cfg)
    n_sent = d.send(alarm_row)
"""
from __future__ import annotations

import json
import smtplib
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from autosys.db.schema import AlarmRow, JobRow
from autosys.db.connection import sync_session
from autosys.notifications.config import NotificationConfig
from autosys.notifications.snmp_notifier import SnmpNotifier
from autosys.notifications.remedy_notifier import RemedyNotifier


class Dispatcher:
    """
    Sends a single AlarmRow to all enabled notification channels.

    Parameters
    ----------
    config:
        ``NotificationConfig`` instance.  If ``None``, a fresh one is
        constructed (reads from environment).
    http_post_fn:
        Injectable for tests — replaces ``urllib.request.urlopen`` calls.
        Signature: ``(url: str, payload: dict) -> bool``
    smtp_send_fn:
        Injectable for tests — replaces the SMTP send logic.
        Signature: ``(config, subject: str, body: str) -> bool``
    """

    def __init__(
        self,
        config:       Optional[NotificationConfig] = None,
        http_post_fn: Optional[callable]            = None,
        smtp_send_fn: Optional[callable]            = None,
        snmp_send_fn: Optional[callable]            = None,
        remedy_post_fn: Optional[callable]          = None,
        max_retries:  int                            = 3,
    ) -> None:
        self._cfg         = config or NotificationConfig()
        self._http_post   = http_post_fn or _default_http_post
        self._smtp_send   = smtp_send_fn or _default_smtp_send
        self._max_retries = max_retries

        # Build SNMP notifier if configured
        self._snmp_notifier: Optional[SnmpNotifier] = None
        if self._cfg.snmp_host:
            self._snmp_notifier = SnmpNotifier(
                host=self._cfg.snmp_host,
                port=self._cfg.snmp_port,
                community=self._cfg.snmp_community,
                send_fn=snmp_send_fn,
            )

        # Build Remedy notifier if configured
        self._remedy_notifier: Optional[RemedyNotifier] = None
        if self._cfg.remedy_url and self._cfg.remedy_token:
            self._remedy_notifier = RemedyNotifier(
                api_url=self._cfg.remedy_url,
                api_token=self._cfg.remedy_token,
                http_post_fn=remedy_post_fn,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, alarm: AlarmRow) -> int:
        """
        Send the alarm to notification channels.

        If the job has a ``notification_type`` set, only the matching
        channel is used.  Otherwise all configured channels are tried
        (backward-compatible behaviour).

        Returns the number of channels that successfully delivered.
        Also sets ``alarm.notified = True`` if at least one channel
        succeeded.
        """
        n = 0
        payload = self._build_payload(alarm)

        # Look up job's notification_type for routing
        job_type = self._get_job_notification_type(alarm.job_name)

        if job_type == "EMAIL":
            if self._cfg.smtp_host and self._cfg.smtp_from:
                if self._send_with_retry(lambda: self._send_email(alarm)):
                    n += 1
        elif job_type == "SNMP":
            if self._snmp_notifier:
                if self._send_with_retry(lambda: self._snmp_notifier.send(alarm)):
                    n += 1
        elif job_type == "NSM":
            if self._cfg.nsm_url:
                if self._send_with_retry(lambda: self._send_nsm(alarm, payload)):
                    n += 1
        elif job_type == "REMEDY":
            if self._remedy_notifier:
                if self._send_with_retry(lambda: self._remedy_notifier.send(alarm)):
                    n += 1
        else:
            # No notification_type on job → send to all configured channels
            if self._cfg.nsm_url:
                if self._send_with_retry(lambda: self._send_nsm(alarm, payload)):
                    n += 1

            if self._cfg.smtp_host and self._cfg.smtp_from and self._cfg.smtp_to_list:
                if self._send_with_retry(lambda: self._send_email(alarm)):
                    n += 1

            if self._cfg.slack_url:
                if self._send_with_retry(lambda: self._send_slack(alarm)):
                    n += 1

            if self._cfg.pd_routing_key:
                if self._send_with_retry(lambda: self._send_pagerduty(alarm)):
                    n += 1

            if self._snmp_notifier:
                if self._send_with_retry(lambda: self._snmp_notifier.send(alarm)):
                    n += 1

            if self._remedy_notifier:
                if self._send_with_retry(lambda: self._remedy_notifier.send(alarm)):
                    n += 1

        if n > 0:
            alarm.notified = True

        return n

    # ------------------------------------------------------------------
    # Retry logic
    # ------------------------------------------------------------------

    def _send_with_retry(self, send_fn: callable) -> bool:
        """
        Call *send_fn* with exponential backoff retry.

        3 attempts: immediate, 1s, 2s.
        Returns True if any attempt succeeds.
        """
        for attempt in range(self._max_retries):
            try:
                if send_fn():
                    return True
            except Exception as exc:
                logger.warning(
                    "Dispatcher: attempt %d/%d failed: %s",
                    attempt + 1, self._max_retries, exc,
                )
            if attempt < self._max_retries - 1:
                backoff = 2 ** attempt  # 1s, 2s, 4s...
                time.sleep(backoff)
        return False

    # ------------------------------------------------------------------
    # Job notification_type lookup
    # ------------------------------------------------------------------

    @staticmethod
    def _get_job_notification_type(job_name: str) -> Optional[str]:
        """Look up the job's notification_type from the DB."""
        try:
            with sync_session() as session:
                row = session.get(JobRow, job_name)
                if row and row.notification_type:
                    return str(row.notification_type).upper()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Channel implementations
    # ------------------------------------------------------------------

    def _send_nsm(self, alarm: AlarmRow, payload: dict) -> bool:
        """POST JSON payload to NSM webhook."""
        try:
            ok = self._http_post(self._cfg.nsm_url, payload)
            if ok:
                logger.debug("Dispatcher: NSM webhook delivered for %r", alarm.alarm_id[:8])
            else:
                logger.warning("Dispatcher: NSM webhook returned non-2xx for %r", alarm.alarm_id[:8])
            return ok
        except Exception as exc:
            logger.warning("Dispatcher: NSM webhook error for %r: %s", alarm.alarm_id[:8], exc)
            return False

    def _send_email(self, alarm: AlarmRow) -> bool:
        """Send plain-text email via SMTP."""
        subject = f"[AutoSys] {alarm.alarm_type}: {alarm.job_name}"
        body    = (
            f"Alarm: {alarm.alarm_type}\n"
            f"Job:   {alarm.job_name}\n"
            f"Run:   {alarm.run_id or '—'}\n"
            f"Time:  {alarm.raised_at}\n\n"
            f"{alarm.message}"
        )
        try:
            ok = self._smtp_send(self._cfg, subject, body)
            if ok:
                logger.debug("Dispatcher: email sent for %r", alarm.alarm_id[:8])
            return ok
        except Exception as exc:
            logger.warning("Dispatcher: SMTP error for %r: %s", alarm.alarm_id[:8], exc)
            return False

    def _send_slack(self, alarm: AlarmRow) -> bool:
        """POST Slack block-kit message to incoming webhook."""
        status_emoji = {
            "ALARM_IF_FAIL":       ":red_circle:",
            "ALARM_IF_TERMINATED": ":warning:",
            "MAX_RUN_ALARM":       ":hourglass:",
            "MIN_RUN_ALARM":       ":fast_forward:",
            "HEARTBEAT_FAIL":      ":computer:",
        }.get(alarm.alarm_type, ":bell:")

        payload = {
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"{status_emoji} *AutoSys Alarm — {alarm.alarm_type}*\n"
                            f"*Job:* `{alarm.job_name}`\n"
                            f"*Message:* {alarm.message}"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                f"alarm_id: `{alarm.alarm_id[:8]}…`  |"
                                f"  raised: {alarm.raised_at}"
                            ),
                        }
                    ],
                },
            ]
        }
        try:
            ok = self._http_post(self._cfg.slack_url, payload)
            if ok:
                logger.debug("Dispatcher: Slack delivered for %r", alarm.alarm_id[:8])
            return ok
        except Exception as exc:
            logger.warning("Dispatcher: Slack error for %r: %s", alarm.alarm_id[:8], exc)
            return False

    def _send_pagerduty(self, alarm: AlarmRow) -> bool:
        """POST PagerDuty Events API v2 trigger."""
        severity = {
            "ALARM_IF_FAIL":       "error",
            "ALARM_IF_TERMINATED": "warning",
            "MAX_RUN_ALARM":       "warning",
            "MIN_RUN_ALARM":       "info",
            "HEARTBEAT_FAIL":      "critical",
        }.get(alarm.alarm_type, "error")

        payload = {
            "routing_key":  self._cfg.pd_routing_key,
            "event_action": "trigger",
            "dedup_key":    f"autosys-{alarm.job_name}-{alarm.alarm_type}",
            "payload": {
                "summary":   f"{alarm.alarm_type}: {alarm.job_name} — {alarm.message}",
                "source":    "autosys-clone",
                "severity":  severity,
                "timestamp": alarm.raised_at.isoformat() + "Z",
                "custom_details": {
                    "alarm_id": alarm.alarm_id,
                    "run_id":   alarm.run_id,
                },
            },
        }
        try:
            ok = self._http_post("https://events.pagerduty.com/v2/enqueue", payload)
            if ok:
                logger.debug("Dispatcher: PagerDuty delivered for %r", alarm.alarm_id[:8])
            return ok
        except Exception as exc:
            logger.warning("Dispatcher: PagerDuty error for %r: %s", alarm.alarm_id[:8], exc)
            return False

    # ------------------------------------------------------------------
    # Payload builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_payload(alarm: AlarmRow) -> dict:
        return {
            "alarm_id":   alarm.alarm_id,
            "job_name":   alarm.job_name,
            "alarm_type": alarm.alarm_type,
            "message":    alarm.message,
            "ts":         alarm.raised_at.isoformat() + "Z",
        }


# ---------------------------------------------------------------------------
# Default channel implementations (using stdlib only — no extra deps)
# ---------------------------------------------------------------------------

def _default_http_post(url: str, payload: dict) -> bool:
    """
    POST a JSON payload to *url*.  Returns True on 2xx, False otherwise.
    """
    data    = json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data    = data,
        headers = {"Content-Type": "application/json"},
        method  = "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        logger.debug("HTTP POST %s → %d", url, exc.code)
        return False
    except Exception as exc:
        logger.debug("HTTP POST %s → error: %s", url, exc)
        return False


def _default_smtp_send(config: NotificationConfig, subject: str, body: str) -> bool:
    """Send a plain-text email using smtplib."""
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"]    = config.smtp_from
    msg["To"]      = ", ".join(config.smtp_to_list)

    try:
        if config.smtp_use_tls:
            server = smtplib.SMTP(config.smtp_host, config.smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP(config.smtp_host, config.smtp_port)

        server.sendmail(config.smtp_from, config.smtp_to_list, msg.as_string())
        server.quit()
        return True
    except Exception as exc:
        logger.warning("SMTP send failed: %s", exc)
        return False
