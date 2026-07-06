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
import urllib.request
import urllib.error
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Optional

from loguru import logger

from autosys.db.schema import AlarmRow
from autosys.notifications.config import NotificationConfig


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
    ) -> None:
        self._cfg         = config or NotificationConfig()
        self._http_post   = http_post_fn or _default_http_post
        self._smtp_send   = smtp_send_fn or _default_smtp_send

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, alarm: AlarmRow) -> int:
        """
        Send the alarm to all configured channels.

        Returns the number of channels that successfully delivered.
        Also sets ``alarm.notified = True`` if at least one channel
        succeeded.
        """
        n = 0
        payload = self._build_payload(alarm)

        if self._cfg.nsm_url:
            if self._send_nsm(alarm, payload):
                n += 1

        if self._cfg.smtp_host and self._cfg.smtp_from and self._cfg.smtp_to_list:
            if self._send_email(alarm):
                n += 1

        if self._cfg.slack_url:
            if self._send_slack(alarm):
                n += 1

        if self._cfg.pd_routing_key:
            if self._send_pagerduty(alarm):
                n += 1

        if n > 0:
            alarm.notified = True

        return n

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
