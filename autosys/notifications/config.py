"""
Notification channel configuration — Phase 10.

All settings are read from environment variables so that no credentials
are ever baked into source code.  Each channel is silently disabled when
its required env-var is absent.

Env vars
--------
NSM_WEBHOOK_URL          URL of an NSM/webhook receiver.  JSON POST.
SMTP_HOST                SMTP relay host (e.g. "smtp.example.com").
SMTP_PORT                SMTP port (default: 25).
SMTP_FROM                Sender address (e.g. "autosys@example.com").
SMTP_TO                  Comma-separated recipient(s).
SMTP_USE_TLS             "true" to enable STARTTLS (default: false).
SLACK_WEBHOOK_URL        Slack incoming-webhook URL.
PD_ROUTING_KEY           PagerDuty Events API v2 routing/integration key.
"""
from __future__ import annotations

import os


class NotificationConfig:
    """
    Reads all notification configuration from the process environment.

    All attributes default to ``None`` / sensible fallback so the
    dispatcher can check ``if cfg.nsm_url`` before attempting a send.
    """

    def __init__(self) -> None:
        self.nsm_url: str | None = os.getenv("NSM_WEBHOOK_URL")

        self.smtp_host:    str | None = os.getenv("SMTP_HOST")
        self.smtp_port:    int        = int(os.getenv("SMTP_PORT", "25"))
        self.smtp_from:    str | None = os.getenv("SMTP_FROM")
        self.smtp_to_list: list[str]  = [
            a.strip()
            for a in os.getenv("SMTP_TO", "").split(",")
            if a.strip()
        ]
        self.smtp_use_tls: bool = os.getenv("SMTP_USE_TLS", "").lower() == "true"

        self.slack_url:     str | None = os.getenv("SLACK_WEBHOOK_URL")
        self.pd_routing_key: str | None = os.getenv("PD_ROUTING_KEY")

        # SNMP (Phase 5)
        self.snmp_host:      str | None = os.getenv("AUTOSYS_SNMP_HOST")
        self.snmp_port:      int        = int(os.getenv("AUTOSYS_SNMP_PORT", "162"))
        self.snmp_community: str        = os.getenv("AUTOSYS_SNMP_COMMUNITY", "public")

        # Remedy (Phase 5)
        self.remedy_url:     str | None = os.getenv("AUTOSYS_REMEDY_API_URL")
        self.remedy_token:   str | None = os.getenv("AUTOSYS_REMEDY_API_TOKEN")

    @property
    def any_enabled(self) -> bool:
        """Return True if at least one channel is configured."""
        return bool(
            self.nsm_url
            or self.smtp_host
            or self.slack_url
            or self.pd_routing_key
            or self.snmp_host
            or self.remedy_url
        )
