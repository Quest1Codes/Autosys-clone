"""Shared HTTP helper: urlopen restricted to http(s).

``urllib.request.urlopen`` also opens ``file://`` and ``ftp://`` URLs. Every
caller in this project only ever means to talk HTTP to an API, and the URLs
come from job definitions or configuration, so the scheme is checked here once
instead of at each call site.
"""
from __future__ import annotations

import urllib.request
from typing import Any, Union


def check_http_url(url: str) -> str:
    """Return ``url`` unchanged if it is http:// or https://, else raise."""
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"Refusing non-HTTP URL: {url!r}")
    return url


def urlopen_http(target: Union[str, urllib.request.Request], timeout: float) -> Any:
    """``urllib.request.urlopen`` that only accepts http(s) URLs."""
    url = target if isinstance(target, str) else target.full_url
    check_http_url(url)
    return urllib.request.urlopen(target, timeout=timeout)  # nosec B310  # nosemgrep: dynamic-urllib-use-detected -- scheme checked above
