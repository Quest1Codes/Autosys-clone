"""
Integration-class classification: rules as data (T15 in the task13
assessment-report research's `07-build-tasks.md`).

Ported from `dev/task13-assessment-report-research/llm-responses/
12-classification-rules-swe2max.md` (re-verified here against this repo's
real storage layer, not trusted as written -- see the fixes below). 91
rules (86 ported + 5 split out while fixing the scope bug below), 66
classes, 10 traits, loaded from `data/classification_rules.json` so a new
product is one data row, not a code change (§3.3 of that research: "No
Python change" is the point).

Classifies **stored jobs**, not re-parsed JIL text (R7 in
`05-vishy-decisions.md`): callers pass the job_type and command already on
a `JobRow`, which have already been through this repo's own
`models.job.normalize_job_type` at import time. This module never
re-implements that normalization -- it only classifies what's already
canonical, so the scan (this module) and the stored manifest can't
disagree about what a job's type is, only about which *integration class*
that type or command implies.

Two things the ported design got wrong, found and fixed while porting,
not invented new:

- R2 ("shell prefixes break command parsing"): the research's own
  `executable()` took the first whitespace token with no regard for shell
  statement separators or wrapper binaries, so `. /etc/profile;
  /app/bin/load.sh` resolved to `.` instead of `load.sh`. `executable()`
  here splits on `;`/`&&`/`&` first and skips a bounded list of wrapper
  executables (`sudo`, `su`, `env`, `nohup`, `exec`, `time`, `.`, `source`).
- 9 "any"-scope token patterns across 5 rules (`jil`, `chase`, `forecast`,
  `oc`, `helm`, `bq`, `cmd`, `sh`, `isql`) misclassified a plain command
  whose destination argument happened to share the name -- e.g.
  `/bin/cp report.csv /archive/jil` read as `scheduler_self_call` (which
  disposition rules then treat as REDESIGN), because `any` scope matches
  any whitespace token's basename, not just the executable. Split into
  exe-scoped sibling rules (`*-AMBIG`), matching the rule schema's own
  stated principle ("Short/ambiguous tokens must be exe", Part B §3.1)
  that these 5 rules didn't actually follow. Checked systematically
  against every "any"-scope pattern, not just these 9 -- the other ~180
  are long, distinctive vendor binary names (`mysqldump`, `spark-submit`)
  where the same coincidence is not a realistic risk, and were
  deliberately left alone. `tests/test_classification.py::TestScopeFix`
  fails against the original scoping and passes against this fix.
- A heredoc body (`direct -s <<EOF submit file=/x; EOF`) contains its own
  `;` with no relation to shell statement boundaries; the R2 fix above
  originally split on it and took the heredoc's own closing `EOF` as the
  command. `executable()` now only splits on separators before any
  `<<MARKER` heredoc introducer.
"""
from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_RULES_PATH = Path(__file__).parent / "data" / "classification_rules.json"

# Wrapper executables that aren't the real work -- skip past them (and one
# bounded run of flags) to find the thing actually being invoked. `.` and
# `source` are shell builtins that load another script into the current
# shell; the loaded script's effect, not the builtin itself, is evidence.
_SHELL_WRAPPERS = frozenset({".", "source", "env", "sudo", "su", "nohup", "exec", "time", "command"})


def _basename(tok: str) -> str:
    tok = tok.strip().strip('"').strip("'")
    return re.split(r"[\\/]", tok)[-1].lower() if tok else ""


_HEREDOC_START = re.compile(r"<<-?\s*['\"]?(\w+)")


def executable(command: str | None) -> str:
    """The program a command actually runs: quote-aware, shell-statement-
    and wrapper-aware (R2's fix). Never the directory, never the arguments.
    """
    text = (command or "").strip()
    if not text:
        return ""
    # A heredoc body (`<<EOF ... EOF`) can itself contain ';'/'&&' with no
    # relation to shell statement boundaries (found while testing this fix
    # against "direct -s <<EOF submit file=/x; EOF" -- splitting on that
    # ';' took the heredoc's own closing "EOF" as the command). Only split
    # on separators that appear before any heredoc marker starts.
    heredoc = _HEREDOC_START.search(text)
    splittable, rest = (text[:heredoc.start()], text[heredoc.start():]) if heredoc else (text, "")
    # Shell statement separators: real work tends to be the LAST statement
    # (". /etc/profile; /app/bin/load.sh" sources environment, then runs
    # the actual job); take the last non-empty segment.
    segments = re.split(r"(?:;|&&|&(?!&))", splittable)
    segment = next((s.strip() for s in reversed(segments) if s.strip()), splittable) or text
    del rest  # the heredoc body is deliberately never split or scanned for a separator

    toks = _shell_split(segment)
    i = 0
    while i < len(toks):
        tok = toks[i]
        base = _basename(tok)
        if base not in _SHELL_WRAPPERS:
            return base
        # Skip the wrapper and any flags (-u svc, -E, ...) up to the next
        # non-flag token, which is either the real executable or another
        # wrapper (sudo env /app/bin/x keeps unwrapping).
        i += 1
        while i < len(toks) and toks[i].startswith("-"):
            i += 1
    return ""


def _shell_split(segment: str) -> list[str]:
    """Whitespace tokens, respecting a leading double-quoted first token
    (the one shape this repo's commands are known to use for paths with
    spaces -- same assumption the ported research made)."""
    segment = segment.strip()
    if not segment:
        return []
    if segment[0] == '"':
        end = segment.find('"', 1)
        first = segment[1:end] if end > 0 else segment[1:]
        rest = segment[end + 1:].split() if end > 0 else []
        return [first] + rest
    return segment.split()


# ---------------------------------------------------------------------------
# Credential redaction and shape flags (Part F safety design)
# ---------------------------------------------------------------------------

_KV_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|apikey|api[-_]?key|credential|auth)\s*[=:]\s*\S+"
)
_USERPASS_AT = re.compile(r"\b[\w.-]+:[^/\s:@]+@")
# Short secret flags (-p, -w, -pw) only count as credential evidence under
# an executable known to read a secret that way -- otherwise `--profile`
# or `-w` (width) would be a false flag.
_CRED_FLAG_EXES = frozenset({
    "sshpass", "psftp", "lftp", "ncftp", "mysql", "psql", "vsql", "nzsql",
    "snowsql", "sqlplus", "sqlcmd", "osql", "isql", "bteq", "dbaccess",
    "curl", "ascp", "winscp.com", "pmcmd", "dsjob", "psae", "db2",
})
_FLAGPASS = re.compile(r"(?i)(?<![\w-])-(-?p(?:ass(?:word)?)?|w|pw|CP)\s*=?\s*\S+")
_USERPASS_PAIR = re.compile(r"(?i)\b-u\s+\S*:[^@\s,]+\S*")
_DBCONN = re.compile(
    r"(?i)\b(sqlplus|expdp|impdp|exp|imp|mysql|psql|sqlcmd|bcp|osql|isql|bteq|"
    r"vsql|nzsql|snowsql|dbaccess|beeline|impala-shell|auto_ps7|concsub|fndload|psae)"
    r"\s+[^\n]*?\b[\w$%.-]+/[^\s]+"
)
_SSHPASS = re.compile(r"(?i)\bsshpass\s+-p\s*\S+")
_SCRIPTFILE = re.compile(r"(?i)-s:\S+|/script\s*=\s*\S+|/script\s+\S+")
_FTP_VERB = re.compile(r"(?i)^(get|put|mget|mput|send|recv|sget|sput|upload|download)$")


def _positional_ftp_cred(exe_l: str, cmd: str) -> bool:
    """`<ftp-like exe> <verb> <host> <user> <secret> <paths>` carries no flag
    marker, so any ftp/scp-named executable with a transfer verb and 6+
    tokens is flagged by shape rather than guessing which token is the
    secret."""
    if "ftp" not in exe_l and "scp" not in exe_l:
        return False
    toks = cmd.split()
    if len(toks) < 6 or not _FTP_VERB.match(toks[1].lower().strip('"')):
        return False
    return all(re.fullmatch(r"[A-Za-z0-9._%-]{3,}", t.strip('"')) for t in toks[3:5])


def redact_and_flag(command: str | None, exe: str = "") -> tuple[str, set[str]]:
    """(redacted command, credential-shape flags). Secrets are removed, not
    hashed -- nothing downstream can recover them. A flag is evidence of a
    *shape*, never proof a credential exists."""
    flags: set[str] = set()
    out = command or ""
    if _KV_SECRET.search(out):
        flags.add("inline_kv_secret")
        out = _KV_SECRET.sub(r"\1=<redacted>", out)
    if _USERPASS_AT.search(out):
        flags.add("inline_userpass_at")
        out = _USERPASS_AT.sub("<user:pass>@", out)
    exe_l = _basename(exe)
    exe_cred = (exe_l in _CRED_FLAG_EXES or (exe_l.endswith(".exe") and exe_l[:-4] in _CRED_FLAG_EXES)
                or "ftp" in exe_l or "scp" in exe_l)
    if exe_cred and _FLAGPASS.search(out):
        flags.add("inline_flag_secret")
        out = _FLAGPASS.sub(r"\1=<redacted>", out)
    if _SSHPASS.search(out):
        flags.add("sshpass_inline")
        out = _SSHPASS.sub("sshpass -p <redacted>", out)
    if _USERPASS_PAIR.search(out):
        flags.add("inline_userpass_flag")
        out = _USERPASS_PAIR.sub("-u <user:pass>", out)
    if _DBCONN.search(out):
        flags.add("inline_db_connect")
    if _SCRIPTFILE.search(out):
        flags.add("credential_via_scriptfile")
    if _positional_ftp_cred(exe_l, out):
        flags.add("positional_credential_shape")
    return out, flags


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleSet:
    meta: dict[str, Any]
    classes: dict[str, dict]
    rules: list[dict]          # sorted by (priority, id)
    traits: list[dict]
    unclassified: dict[str, Any]


@lru_cache(maxsize=1)
def load_rules(path: Path = _RULES_PATH) -> RuleSet:
    """Loaded once per process and cached: the file doesn't change at
    runtime, and re-parsing it per job would be wasted work at estate
    scale. `load_rules.cache_clear()` if a test needs a different file."""
    d = json.loads(Path(path).read_text())
    rules = sorted(d["rules"], key=lambda r: (r["priority"], r["id"]))
    return RuleSet(meta=d["rule_set"], classes=d["classes"], rules=rules,
                   traits=d["traits"], unclassified=d["unclassified"])


def _tokens(redacted_cmd: str) -> set[str]:
    out = set()
    for t in redacted_cmd.split():
        b = _basename(t)
        if b:
            out.add(b)
    return out


def classify_job(job_type: str, command: str | None, *, user_type: str | None = None,
                  rule_set: RuleSet | None = None) -> dict[str, Any]:
    """Classify one already-stored job. `job_type` must already be
    canonical (as stored on `JobRow.job_type`, i.e. already through
    `models.job.normalize_job_type`) -- this function does no further
    type normalization of its own, on purpose, so it can never disagree
    with how the job was actually stored.

    `user_type` is the site's declared type name when `job_type ==
    "USERDEFINED"` (JobRow.extra_attrs_json's `user_job_type`), carried
    through as evidence, not re-derived.

    Returns the per-job manifest fields from Part B §3.5 of the research:
    job_type, user_type, class, rule_id, confidence, evidence, all_rule_ids,
    traits, executable. No command text, no arguments, no job name -- the
    caller already has those if it wants them; this function never needs
    or stores them.
    """
    rs = rule_set or load_rules()
    raw_type = (job_type or "CMD").strip().upper()
    raw_cmd = command or ""
    exe = executable(raw_cmd).lower()
    redacted, cred_flags = redact_and_flag(raw_cmd, exe)
    tokset = _tokens(redacted)

    matches = []
    for r in rs.rules:
        sig, mt, pat = r["signal"], r["match"], r["pattern"]
        hit = None
        if sig == "job_type":
            if raw_type in pat:
                hit = ("job_type", raw_type)
        elif mt == "exe_exact":
            if exe in pat or exe.removesuffix(".exe") in pat:
                hit = ("executable", exe)
        elif mt == "exe_glob":
            if any(fnmatch.fnmatch(exe, p) for p in pat):
                hit = ("executable", exe)
        elif mt == "token":
            if r.get("scope", "any") == "exe":
                if exe in pat or exe.removesuffix(".exe") in pat:
                    hit = ("token", exe)
            else:
                matched = next((p for p in pat if p in tokset or f"{p}.exe" in tokset), None)
                if matched:
                    hit = ("token", matched)
        elif mt == "ext":
            if "." in exe and exe.rsplit(".", 1)[-1] in pat:
                hit = ("extension", exe.rsplit(".", 1)[-1])
        elif mt == "regex":
            if re.search(pat, redacted, re.IGNORECASE):
                hit = ("regex", pat[:30])
        if hit:
            matches.append((r, hit))

    traits = set(cred_flags)
    for t in rs.traits:
        if t["signal"] == "command" and t["match"] == "regex" and re.search(t["pattern"], raw_cmd, re.IGNORECASE):
            traits.add(t["id"])

    primary = min(matches, key=lambda m: (m[0]["priority"], m[0]["id"])) if matches else None
    return {
        "job_type": raw_type,
        "user_type": user_type,
        "class": primary[0]["class"] if primary else "unclassified",
        "rule_id": primary[0]["id"] if primary else None,
        "confidence": primary[0]["confidence"] if primary else None,
        "evidence": primary[1] if primary else None,
        "all_rule_ids": [m[0]["id"] for m in matches],
        "traits": sorted(traits),
        "executable": exe,
    }


def classify_row(row) -> dict[str, Any]:
    """Classify a stored `JobRow` directly -- the "stored jobs only" path
    T15 asks for. `row.job_type` is already canonical (set by
    `models.job.parse_job` at import time); `user_job_type` comes back out
    of `extra_attrs_json` the same way `db.repository._row_to_job` reads it,
    without needing a full Job model validation pass just to classify."""
    extras: dict[str, Any] = {}
    if row.extra_attrs_json:
        try:
            extras = json.loads(row.extra_attrs_json)
        except (TypeError, ValueError):
            pass
    return classify_job(row.job_type, row.command, user_type=extras.get("user_job_type"))
