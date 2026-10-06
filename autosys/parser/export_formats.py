"""
Non-JIL AutoSys exports that come with a JIL estate (audit SEM-09, PARSER-07).

A client's export is more than ``autorep -J ALL -q``. Calendars come from
``autocal_asc`` and global variables from ``autorep -G ALL`` (or a script of
``sendevent -E SET_GLOBAL`` lines). Without them every ``run_calendar`` and
every ``v(...)`` condition points at nothing. This module reads them into
blocks that :mod:`autosys.parser.jil_ingest` archives and stores like JIL
stanzas.

autocal_asc, standard calendar::

    calendar: US_HOLIDAYS
    01/01/2025 00:00
    07/04/2025 00:00

autocal_asc, extended calendar (rules, not dates)::

    extended_calendar: BUS_DAYS
    workday: mo,tu,we,th,fr
    holiday: US_HOLIDAYS
    condition: WORKD

Extended calendars are archived and flagged, not simulated: their rules are
not evaluated, so a job using one does not fire in the simulator.

Globals::

    sendevent -E SET_GLOBAL -G "GL_READY=Y"

    Global Name        Value     Last Changed
    ______________     ________  ___________________
    GL_READY           Y         01/02/2025 10:00:00
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

_CAL_HEADER_RE = re.compile(r"^\s*(calendar|extended_calendar)\s*:\s*(\S+)\s*$", re.IGNORECASE)
_SET_GLOBAL_RE = re.compile(r"^\s*sendevent\b.*?-E\s+SET_GLOBAL\b(?P<rest>.*)$", re.IGNORECASE)
_G_QUOTED_RE = re.compile(r'-G\s+"(?P<n>[^"=]+)=(?P<v>[^"]*)"')
_G_BARE_RE = re.compile(r"-G\s+(?P<n>[^\s\"=]+)=(?P<v>\S*)")
_G_NAME_V_RE = re.compile(r'-G\s+"?(?P<n>[^\s"=]+)"?\s+-v\s+(?:"(?P<qv>[^"]*)"|(?P<v>\S+))')
_TABLE_HEADER_RE = re.compile(r"^\s*Global\s+Name\b", re.IGNORECASE)
_RULE_RE = re.compile(r"^[\s_]+$")


@dataclass
class Block:
    """One definition from a non-JIL export, with its source lines."""
    kind: str                         # calendar | extended_calendar | set_global | other
    name: Optional[str]
    start_line: int
    end_line: int
    raw_text: str
    dates: list[str] = field(default_factory=list)       # ISO dates (calendar)
    attrs: dict = field(default_factory=dict)            # extended calendar rules
    value: Optional[str] = None                          # set_global
    issues: list = field(default_factory=list)


def parse_calendar_date(token: str) -> date:
    """``YYYY-MM-DD`` or autocal's ``MM/DD/YYYY`` (any time after it ignored)."""
    t = token.strip().split()[0]
    if "/" in t:
        return datetime.strptime(t, "%m/%d/%Y").date()
    return date.fromisoformat(t)


def _meaningful(lines: list[str]) -> list[str]:
    return [ln for ln in lines if ln.strip() and not ln.lstrip().startswith(("#", "/*"))]


def detect(text: str) -> Optional[str]:
    """``"autocal"``, ``"globals"`` or ``None`` (treat as JIL)."""
    lines = _meaningful(text.splitlines())
    if not lines:
        return None
    if _CAL_HEADER_RE.match(lines[0]):
        return "autocal"
    if _TABLE_HEADER_RE.match(lines[0]):
        return "globals"
    if all(_SET_GLOBAL_RE.match(ln) for ln in lines):
        return "globals"
    return None


def _split(text: str, starts: list[int]) -> list[tuple[int, int, str]]:
    """Cut *text* into contiguous line ranges beginning at each 0-based index
    in *starts* (lines before the first start join the first range), so the
    blocks' raw texts concatenate back to the input."""
    pieces = text.splitlines(keepends=True)
    if not starts:
        return [(1, len(pieces), text)] if pieces else []
    bounds = [0] + starts[1:] + [len(pieces)]
    out = []
    for a, b in zip(bounds, bounds[1:]):
        out.append((a + 1, b, "".join(pieces[a:b])))
    return out


def parse_autocal(text: str) -> list[Block]:
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if _CAL_HEADER_RE.match(ln)]
    blocks = []
    for (a, b, raw), s in zip(_split(text, starts), starts):
        m = _CAL_HEADER_RE.match(lines[s])
        kind, name = m.group(1).lower(), m.group(2)
        blk = Block(kind, name, a, b, raw)
        for n in range(s + 1, b):
            line = lines[n].strip()
            if not line or line.startswith(("#", "/*")):
                continue
            if kind == "calendar":
                try:
                    blk.dates.append(parse_calendar_date(line).isoformat())
                except ValueError:
                    blk.issues.append({"severity": "warning", "code": "invalid_calendar_date",
                                       "line": n + 1, "message": f"not a date: {line!r}"})
            else:
                key, _, value = line.partition(":")
                blk.attrs[key.strip().lower()] = value.strip()
        if kind == "extended_calendar":
            blk.issues.append({
                "severity": "warning", "code": "extended_calendar_not_simulated", "line": a,
                "message": "extended calendar rules are archived but not evaluated; jobs "
                           "using this calendar will not fire in the simulator"})
        blocks.append(blk)
    return blocks


def _table_columns(rule: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r"_+", rule)]


def parse_globals(text: str) -> list[Block]:
    lines = text.splitlines(keepends=True)
    blocks: list[Block] = []
    cols: list[tuple[int, int]] = []
    for n, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r\n")
        m = _SET_GLOBAL_RE.match(line)
        if m:
            rest = m.group("rest")
            g = _G_QUOTED_RE.search(rest) or _G_BARE_RE.search(rest)
            nv = _G_NAME_V_RE.search(rest)
            if g:
                blocks.append(Block("set_global", g.group("n").strip(), n, n, raw, value=g.group("v")))
            elif nv:
                v = nv.group("qv") if nv.group("qv") is not None else nv.group("v")
                blocks.append(Block("set_global", nv.group("n"), n, n, raw, value=v))
            else:
                blocks.append(Block("other", None, n, n, raw, issues=[{
                    "severity": "warning", "code": "unparsed_set_global", "line": n,
                    "message": "SET_GLOBAL without a NAME=value"}]))
            continue
        if cols and line.strip() and not _RULE_RE.match(line):
            name = line[cols[0][0]:cols[1][0] if len(cols) > 1 else None].strip()
            end = cols[2][0] if len(cols) > 2 else None
            value = line[cols[1][0]:end].strip() if len(cols) > 1 else ""
            if name:
                blocks.append(Block("set_global", name, n, n, raw, value=value))
                continue
        if _RULE_RE.match(line) and "_" in line:
            cols = _table_columns(line)
        blocks.append(Block("other", None, n, n, raw))        # header, rule, blank
    return blocks
