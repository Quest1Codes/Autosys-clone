"""
Glob substitution — replaces %%GLOB:name%% placeholders in job commands
with the content of the corresponding GlobRow.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

from autosys.db.repository import globs2 as glob_repo


_GLOB_PATTERN = re.compile(r"%%GLOB:([A-Za-z0-9_]+)%%")


def substitute_globs(session: Session, command: str) -> str:
    """
    Replace all %%GLOB:name%% placeholders in *command* with the content
    of the corresponding glob from the database.

    If a glob is not found, the placeholder is left unchanged.
    """
    def _replace(match: re.Match) -> str:
        name = match.group(1)
        row = glob_repo.get(session, name)
        if row is None:
            return match.group(0)
        return row.content

    return _GLOB_PATTERN.sub(_replace, command)
