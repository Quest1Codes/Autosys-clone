"""Job type command template expansion — replaces {PLACEHOLDER} with values."""
from __future__ import annotations
import re

_PATTERN = re.compile(r"\{([A-Za-z0-9_]+)\}")


def expand_command_template(template: str, params: dict[str, str]) -> str:
    """Replace {PLACEHOLDER} patterns in template with values from params.

    Unmatched placeholders are left as-is.
    """
    def _replace(match: re.Match) -> str:
        key = match.group(1)
        return params.get(key, match.group(0))
    return _PATTERN.sub(_replace, template)
