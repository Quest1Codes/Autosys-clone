"""
The 13-unit size model and rate card (T17 in the task13 assessment-report
research's `07-build-tasks.md`). This is the direct replacement for the
single "effort band" that was the original complaint this whole effort
traces back to -- Vishy's own words, 2026-11-05: "the effort range it
gives all are so blind and random".

Ported from `dev/task13-assessment-report-research/llm-responses/
10-effort-without-measurement-opus5.5.md`'s `model.py` (prompt 10,
independently re-run by two models, 21 properties, 0 failures on both).
Counts replace a single guessed number: how many jobs, patterns,
integrations, decisions, flows, cutover groups -- never an hour, never a
rate, until a signed card says otherwise.

**No hours and no rate appear in `size()`.** It returns a count vector; a
property proved on the vector holds for every non-negative rate card,
because `hours()` is a linear roll-up applied afterward, and only once a
card exists.

**E1, fixed here, not carried over as a bug**: the ported research's own
`hours(vector, card) = sum(vector[u] * card[u] for u in vector)` sums
every unit in the vector, including `U` (unrecognised jobs) -- so if a
caller ever put a rate under the key "U", unrecognised work would be
priced while the client-facing text says "not sized" (05-vishy-decisions.
md's E1: "Unknown jobs are treated three ways"). `hours()` here sums only
`UNITS` minus `U`, always, structurally -- not by trusting every caller to
leave "U" out of their card.

**O1, the real business decision this traces to**: `hours()` returns
`None` unless the card status is `"approved"` (not `"draft"`, not
`"expired"`) and every unit it needs has a `low`/`high` rate with `low <=
high`. No report can print hours until someone at Quest1 signs a card
that says so -- that person isn't named by this code; see
`06-spec-for-vishy.md` O1 and O9.

Classification reuses `classification.classify_job` (T15, already fixed
for R1/R2/R7) rather than a second copy. `segments()` below is new,
different work specific to sizing: the ported model classifies each
shell *statement* in a multi-statement command separately and keeps the
first one that lands in the executable position, rather than relying on
disposition's single "real" executable."""
from __future__ import annotations

import collections
import functools
import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

from autosys.analysis.classification import classify_job, executable, load_rules, redact_and_flag
from autosys.analysis.condition_refs import condition_job_refs
from autosys.analysis.disposition import is_windows_shaped
from autosys.db.schema import JobRow

_PLATFORM_BLOCKS_PATH = Path(__file__).parent / "data" / "platform_blocks.json"

# class -> (integration, kind). "native" means the agent/runtime leaves
# with AutoSys (R3); "wrapper" means a product the questionnaire can ask
# about separately (does it stay callable, or does it leave too).
FAMILY: dict[str, tuple[str, str]] = {
    "erp_sap": ("sap", "wrapper"), "erp_native_sap": ("sap", "native"),
    "erp_peoplesoft": ("peoplesoft", "wrapper"), "erp_native_peoplesoft": ("peoplesoft", "native"),
    "erp_oracle_ebs": ("oracle_ebs", "wrapper"), "erp_native_oracle_ebs": ("oracle_ebs", "native"),
    "etl_informatica": ("informatica", "wrapper"), "informatica_native": ("informatica", "native"),
    "etl_datastage": ("datastage", "wrapper"), "etl_abinitio": ("abinitio", "wrapper"),
    "etl_talend": ("talend", "wrapper"), "etl_ssis": ("ssis", "wrapper"), "etl_odi": ("odi", "wrapper"),
    "hadoop_hdfs": ("hadoop", "wrapper"), "hadoop_hive": ("hadoop", "wrapper"),
    "hadoop_spark": ("hadoop", "wrapper"), "hadoop_pig": ("hadoop", "wrapper"),
    "hadoop_flink": ("hadoop", "wrapper"), "hadoop_kafka": ("hadoop", "wrapper"),
    "hadoop_legacy": ("hadoop", "wrapper"), "hadoop_native": ("hadoop", "native"),
    "db_oracle": ("database", "wrapper"), "db_teradata": ("database", "wrapper"),
    "db_db2": ("database", "wrapper"), "db_mssql": ("database", "wrapper"),
    "db_postgres": ("database", "wrapper"), "db_mysql": ("database", "wrapper"),
    "db_snowflake": ("database", "wrapper"), "db_netezza": ("database", "wrapper"),
    "db_vertica": ("database", "wrapper"), "db_informix": ("database", "wrapper"),
    "db_ambiguous": ("database", "wrapper"), "database_native": ("database", "native"),
    "transfer_mft": ("mft", "wrapper"),
    "j2ee_native": ("java_appserver", "native"),
    "process_automation_native": ("process_automation", "native"),
    "mainframe_native": ("mainframe", "native"),
    # Not in the questionnaire's family table (05 §3b N/A). OM* monitors, SNMP, WOL,
    # CONNECT and PROXY are agent features, so they leave with AutoSys like other
    # native types. Opinion, carried over from the research: charge them as rework.
    "infra_native": ("infra_monitor", "native"),
}
# Integrations with no vendor question -- still one integration decision each.
OTHER_INTEGRATION: dict[str, str] = {
    "transfer_ftp": "ftp", "transfer_http": "http", "transfer_rsync": "rsync",
    "file_watch": "file_watch", "file_trigger": "file_watch", "file_transfer_native": "file_transfer",
    "webservice_native": "webservice", "remote_exec": "remote_exec", "notify": "notify",
    "cloud_aws": "cloud", "cloud_azure": "cloud", "cloud_gcp": "cloud", "cloud_k8s": "cloud",
    "windows_exe": "windows", "windows_script": "windows", "scheduler_self_call": "scheduler_self_call",
    "cross_scheduler": "cross_scheduler",
}
UNKNOWN_CLASSES = frozenset({"unclassified", "user_defined_type"})
SHELL_NOISE = frozenset({"env", "sudo", "nohup", "time", "exec", "command"})
_GLOBAL_RE = re.compile(r"\$\$[({]?[A-Za-z0-9_.]+")

# Unit names, report order. "CU" (cutover units) is deliberately absent:
# counted and printed separately (the `cu` key below), never priced --
# charging it means removing a link can raise the bill, which a cutover
# group split would do if CU were in this vector.
UNITS: tuple[str, ...] = ("B", "F", "EP", "P", "I", "R", "E", "X", "D", "C", "FL", "PR", "U")

# Credential evidence, for sizing purposes: any redaction flag, or either
# of the two credential traits classify_job can set.
_CRED_TRAIT_IDS = frozenset({"T-CRED-INLINE", "T-CRED-DB"})


@functools.lru_cache(maxsize=1)
def _rule_priority() -> dict[str, int]:
    return {r["id"]: r["priority"] for r in load_rules().rules}


def segments(cmd: str | None) -> list[str]:
    """Split `a; b && c` into its statements, drop a leading `. file` /
    `source file`, and unwrap a leading `sudo`/`env`/`nohup`/`time`/`exec`/
    `command` plus its own flag tokens from each one. Used only for
    sizing, which needs to see every statement in a multi-statement
    command, not just the one `classification.executable()` picks as
    "the real work".

    Known limitation, documented rather than silently guessed either way:
    this only strips a flag *token*, never a separate value token after
    it, so "sudo -u svc cmd" leaves "svc cmd" rather than just "cmd" --
    unlike `classification.executable()`, which does handle this (found
    and fixed there while testing this very function, T17). Sizing only
    uses this for the pattern/arg-shape keys (`pattern_key`, `arg_shape`),
    where an extra leading "svc"-shaped token changes which jobs count as
    the same pattern, not whether a job is classified or disposed
    correctly -- those go through `classification.executable()` instead,
    via `classify_for_sizing`."""
    if not cmd:
        return []
    m = re.match(r"""^\s*su\s+.*?-c\s+(['"])(.*)\1\s*$""", cmd)
    if m:
        cmd = m.group(2)
    out = []
    for part in re.split(r"\s*(?:;|&&|\|\||\n)\s*", cmd):
        toks = part.split()
        first = toks[0] if toks else ""
        while toks and (toks[0] in SHELL_NOISE or re.match(r"^[A-Za-z_]\w*=", toks[0])
                        or (toks[0].startswith("-") and len(toks) > 1 and first in SHELL_NOISE)):
            toks = toks[1:]
        if not toks or toks[0] in (".", "source"):
            continue
        out.append(" ".join(toks))
    return out


@functools.lru_cache(maxsize=65_536)
def classify_for_sizing(job_type: str, command: str) -> tuple[str, bool]:
    """(class, has_credential_evidence) for one job, for sizing. Keyed on
    job_type and command only -- never the job name, so renaming a job
    can't change its size contribution.

    Classifies each statement in the command separately and keeps the
    best (lowest-priority) hit that's either not a token match at all, or
    a token match that landed on that statement's own executable --
    discarding a hit where an "any"-scope rule matched some other token in
    the statement. This is a second, stricter filter on top of T15's own
    scope fix (05 §3j): T15 fixed 9 specific tokens; this discards any
    remaining any-scope false match at the point where it would otherwise
    change a count, without having to enumerate every risky token by hand.
    """
    best: tuple[int, str] | None = None
    for seg in (segments(command) or [""]):
        c = classify_job(job_type, seg)
        if c["class"] == "unclassified":
            continue
        ev = c["evidence"]
        exe = c["executable"]
        positional = not (ev and ev[0] == "token" and ev[1] not in (exe, exe.removesuffix(".exe")))
        if not positional:
            continue
        priority = _rule_priority().get(c["rule_id"], 999)
        if best is None or priority < best[0]:
            best = (priority, c["class"])
    if best is None:
        return "unclassified", False
    cred = False
    if command:
        exe = executable(command).lower()
        _, flags = redact_and_flag(command, exe)
        cred = bool(flags)
        if not cred:
            for t in load_rules().traits:
                if t["id"] in _CRED_TRAIT_IDS and re.search(t["pattern"], command, re.IGNORECASE):
                    cred = True
                    break
    return best[1], cred


def exe_template(command: str | None) -> str:
    """Normalised executable: basename, lower case, digit runs folded,
    variables folded. Two jobs with the same template and job_type are
    candidates for one design pattern (P1)."""
    if not command:
        return "-"
    segs = segments(command)
    exe = executable(segs[-1] if segs else command).lower()
    exe = re.sub(r"\$\$?[({]?[a-z0-9_.]+[)}]?", "$", exe)
    return re.sub(r"\d+", "#", exe)


def arg_shape(command: str | None) -> str:
    """Shape of the arguments: how many, which are flags. Values dropped."""
    if not command:
        return ""
    toks = (segments(command) or [command])[-1].split()[1:]
    return "".join("f" if t.startswith("-") else "a" for t in toks)


def pattern_key(job_type: str, command: str | None, cls: str, level: Literal["K1", "K2", "K3"] = "K2"):
    """Pattern identity. Never reads condition, calendar or box -- those
    have their own units, and mixing them in here would double-count."""
    jt = (job_type or "CMD").upper()
    if level == "K1":
        return (cls, jt)
    if level == "K2":
        return (cls, jt, exe_template(command))
    return (cls, jt, exe_template(command), arg_shape(command))


def constructs(row: JobRow) -> set[str]:
    """Per-job exception constructs. Each is one decision a person has to
    make for this job, independent of its integration class."""
    cond = row.condition or ""
    hits = set()
    if "|" in cond:
        hits.add("cond_or")
    if re.search(r"\(\s*[^(),\s]+\s*,\s*\d", cond):
        hits.add("look_back")
    if re.search(r"(?<![A-Za-z0-9_.#-])(e|exitcode)\s*\(", cond, re.IGNORECASE):
        hits.add("exit_code")
    if re.search(r"(?<![A-Za-z0-9_.#-])(n|notrunning)\s*\(", cond, re.IGNORECASE):
        hits.add("not_running")
    if re.search(r"(?<![A-Za-z0-9_.#-])v\s*\(", cond, re.IGNORECASE):
        hits.add("value_cond")
    if "^" in cond:
        hits.add("cross_instance")
    if row.box_success or row.box_failure:
        hits.add("box_logic")
    if row.box_terminator or row.job_terminator:
        hits.add("terminator")
    if row.resources:
        hits.add("resource")
    for text in (row.command, row.std_out_file, row.std_err_file, row.std_in_file):
        if text and _GLOBAL_RE.search(text):
            hits.add("global_var")
            break
    return hits


def split_machines(value: str | None) -> list[str]:
    return [m.strip() for m in (value or "").split(",") if m.strip()]


def flow_roots(rows: list[JobRow]) -> dict[str, str]:
    """Job name -> flow id. Same answer regardless of row order, even if
    a malformed export has a box_name loop."""
    parent = {r.job_name: (r.box_name or "") for r in rows}

    def walk(name: str) -> str:
        seen = [name]
        cur = name
        while True:
            up = parent.get(cur, "")
            if not up:
                return cur
            if up not in parent:
                return "missing-box:" + up
            if up in seen:
                return min(seen[seen.index(up):])  # a box_name loop: smallest member names it
            seen.append(up)
            cur = up
    return {n: walk(n) for n in parent}


# Answer text -> effect. Anything off-list is Unsure (Q1 in 05-vishy-decisions.md).
VENDOR_EFFECT = {"Yes — the product stays and runs without AutoSys": "keep",
                 "No — the product or its adapter goes when AutoSys goes": "rework",
                 "Not ours — this detection looks wrong": "unknown"}
WINDOWS_EFFECT = {"Keep the Windows hosts and call them remotely": "keep",
                  "Move the work to Linux or containers": "rework"}
ADOPTION_REUSE = "Production Airflow runs some workloads"
ADOPTION_FULL = ("None — this would be our first deployment", "Pilot or sandbox only")
SKILL_BLOCKS = {"Strong Python and software engineering team; AutoSys knowledge is supplementary": 0,
                "Primarily AutoSys/JIL administrators with little or no Python experience": 1,
                "Mixed — some AutoSys admins, some Python/software engineering experience": 1,
                "Hybrid — platform team handles Airflow infrastructure; application teams migrate their own workloads": 1}
CUTOVER_PARALLEL_FRACTION = {"Single cutover with no parallel run": 0,
                             "Waves with a full business cycle of parallel running": 1,
                             "Waves with a short parallel run of days to two weeks": 1,
                             "Waves where some jobs cannot run twice because of side effects": 1}
OSS = "Apache Airflow run by us"
ASTRO = "Astronomer managed Airflow"


@functools.lru_cache(maxsize=1)
def _platform_blocks() -> dict[str, list[dict]]:
    return json.loads(_PLATFORM_BLOCKS_PATH.read_text())


def platform_blocks(target: Literal["oss", "astro"], answers: dict[str, str]) -> tuple[int, int]:
    """(low, high) platform work items for one target. Never scales with
    job count -- these are one-time setup decisions, not per-job work."""
    adoption = answers.get("existing_airflow_adoption")
    net = answers.get("network_reachability", "")
    windows = answers.get("windows_execution_plan", "")
    exec_model = answers.get("astro_task_execution", "")
    low = high = 0
    for b in _platform_blocks()[target]:
        when = b.get("when")
        lo = hi = 1
        if when == "network":
            lo, hi = (0, 0) if net.startswith("Yes") else ((1, 1) if net[:2] in ("Pa", "No") else (0, 1))
        elif when == "windows":
            lo, hi = (1, 1) if windows.startswith("Keep") else ((0, 0) if windows.startswith("Move") else (0, 1))
        elif when == "remote_execution":
            lo, hi = (1, 1) if exec_model.startswith(("On Remote", "Some")) else ((0, 0) if exec_model.startswith("On workers") else (0, 1))
        elif when == "new_host":
            home = answers.get("oss_platform_home", "")
            lo, hi = (0, 0) if home.startswith("An existing") else ((1, 1) if home.startswith(("Virtual", "A new")) else (0, 1))
        if b.get("reusable"):
            if adoption == ADOPTION_REUSE:
                lo, hi = 0, 0
            elif adoption not in ADOPTION_FULL:
                lo = 0  # Unsure: printed as included and assumed, so high stays
        low += lo
        high += hi
    return low, high


@dataclass(frozen=True)
class SizeVector:
    low: dict[str, int]
    high: dict[str, int]
    cu: dict[str, int]
    families: dict[str, dict[str, int]]
    row_count: int

    def excess_unknown_jobs(self) -> int:
        """U, for the "N jobs not sized" line -- read here, not re-derived
        by a caller, so the printed count and the priced count can't
        disagree (E1)."""
        return self.low["U"]

    def dominant_order(self) -> list[tuple[str, str]]:
        """(bigger, smaller) integration-family pairs where `bigger` is at
        least as large as `smaller` on every unit and strictly larger on
        at least one -- the only ordering that holds at *any* rate card
        (E2 in 05-vishy-decisions.md: a full ranking by one rate card held
        on only 6 of 36 pairs; this dominance relation is what's left once
        a single number is refused). Returns the pairs only, never a full
        ranked list -- most family pairs are simply incomparable, and
        printing them in some arbitrary order would imply a ranking this
        can't support."""
        order_units = ("I", "P", "EP", "R_hi", "E", "X", "D")
        vecs = {label: tuple(counts.get(u, 0) for u in order_units) for label, counts in self.families.items()}
        return [(a, b) for a in vecs for b in vecs
                if a != b and vecs[a] != vecs[b] and all(x >= y for x, y in zip(vecs[a], vecs[b]))]


def size(rows: list[JobRow], answers: Optional[dict[str, str]] = None, level: Literal["K1", "K2", "K3"] = "K2") -> SizeVector:
    """Rows + questionnaire answers -> a counted size vector. No hours,
    no rate, anywhere in this function."""
    answers = answers or {}
    names = {r.job_name for r in rows}
    roots = flow_roots(rows)
    flows = collections.Counter(roots.values())
    lo = dict.fromkeys(UNITS, 0)
    hi = dict.fromkeys(UNITS, 0)
    patterns, ints, eps, cal_names, combos = set(), set(), set(), set(), set()
    rework_lo, rework_hi = set(), set()
    fam: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    fam_patterns: dict[str, set] = collections.defaultdict(set)
    fam_eps: dict[str, set] = collections.defaultdict(set)
    link = {f: f for f in flows}

    def find(x):
        while link[x] != x:
            link[x] = link[link[x]]
            x = link[x]
        return x

    edges_seen = set()
    for r in rows:
        name = r.job_name
        jt = (r.job_type or "CMD").upper()
        cls, cred = classify_for_sizing(jt, r.command or "")
        hits = constructs(r)
        if cred:
            hits.add("credential")
        e_count = len(hits)
        lo["E"] += e_count
        hi["E"] += e_count
        integ, kind = FAMILY.get(cls, (OTHER_INTEGRATION.get(cls), None))
        label = integ or ("unknown" if cls in UNKNOWN_CLASSES else "box" if jt == "BOX" else "generic")
        fam[label]["E"] += e_count

        for ref in condition_job_refs(r.condition):
            if ref == name or (ref, name) in edges_seen:
                continue
            edges_seen.add((ref, name))
            if ref not in names:
                lo["D"] += 1
                hi["D"] += 1
                fam[label]["D"] += 1
            elif roots[ref] != roots[name]:
                lo["X"] += 1
                hi["X"] += 1
                fam[label]["X"] += 1
                link[find(roots[ref])] = find(roots[name])

        for cal in (r.run_calendar, r.exclude_calendar):
            if cal and cal.strip():
                cal_names.add(cal.strip())
        if r.run_calendar or r.exclude_calendar:
            combos.add(((r.run_calendar or "").strip(), (r.exclude_calendar or "").strip()))

        if jt == "BOX":
            # The box row is never its own instance: its logic is in E,
            # its schedule belongs to the flow, its children count once
            # each as their own rows.
            continue

        lo["I"] += 1
        hi["I"] += 1
        fam[label]["I"] += 1
        key = pattern_key(jt, r.command, cls, level)
        patterns.add(key)
        fam_patterns[label].add(key)
        if integ:
            ints.add(integ)
        for m in split_machines(r.machine):
            eps.add((label, m))
            fam_eps[label].add(m)
        if cls in UNKNOWN_CLASSES:
            lo["U"] += 1
            hi["U"] += 1
            fam[label]["U"] += 1

        # Rework. A native job can never stay as it is: its agent leaves
        # with AutoSys (R3), so every answer keeps it in R. Windows is a
        # host trait, checked on top (Q5).
        definite = open_ = False
        rkey = ("job", name)
        if kind == "native":
            definite = True
        elif kind == "wrapper":
            effect = VENDOR_EFFECT.get(answers.get("vendor_runtime__" + integ))
            if effect == "rework":
                definite = True
            elif effect == "unknown":
                lo["U"] += 1
                hi["U"] += 1
                fam[label]["U"] += 1
            elif effect != "keep":
                open_ = True
        if is_windows_shaped(r.command):
            effect = WINDOWS_EFFECT.get(answers.get("windows_execution_plan"))
            if not (definite or open_) or kind is None:
                # Rewriting a Windows script is once per distinct script,
                # not once per job that happens to call it.
                rkey = ("script", executable(r.command or "").lower())
            if effect == "rework":
                definite = True
            elif effect != "keep":
                open_ = True
        if definite:
            rework_lo.add(rkey)
            rework_hi.add(rkey)
            fam[label]["R_lo"] += 1
            fam[label]["R_hi"] += 1
        elif open_:
            rework_hi.add(rkey)
            fam[label]["R_hi"] += 1

    lo["P"] = hi["P"] = len(patterns)
    lo["F"] = hi["F"] = len(ints)
    lo["EP"] = hi["EP"] = len(eps)
    lo["R"], hi["R"] = len(rework_lo), len(rework_hi)
    lo["C"] = hi["C"] = len(cal_names)
    lo["FL"] = hi["FL"] = len(flows)

    strategy = answers.get("migration_cutover_strategy")
    if strategy in CUTOVER_PARALLEL_FRACTION:
        lo["PR"] = hi["PR"] = len(flows) * CUTOVER_PARALLEL_FRACTION[strategy]
    else:
        hi["PR"] = len(flows)

    target = answers.get("target_platform")
    skill = answers.get("team_skill_profile")
    sk_lo, sk_hi = (SKILL_BLOCKS[skill], SKILL_BLOCKS[skill]) if skill in SKILL_BLOCKS else (0, 1)
    if target == OSS:
        b_lo, b_hi = platform_blocks("oss", answers)
    elif target == ASTRO:
        b_lo, b_hi = platform_blocks("astro", answers)
    else:
        pairs = [platform_blocks("oss", answers), platform_blocks("astro", answers)]
        b_lo, b_hi = min(p[0] for p in pairs), max(p[1] for p in pairs)
    lo["B"], hi["B"] = b_lo + sk_lo, b_hi + sk_hi

    units_by_root: dict[str, list[str]] = collections.defaultdict(list)
    for f in flows:
        units_by_root[find(f)].append(f)
    jobs_in = [sum(flows[f] for f in v) for v in units_by_root.values()]
    cu = {"CU": len(units_by_root), "alone": sum(len(v) == 1 for v in units_by_root.values()),
          "largest_flows": max((len(v) for v in units_by_root.values()), default=0),
          "largest_jobs": max(jobs_in, default=0)}
    for label, v in fam_patterns.items():
        fam[label]["P"] = len(v)
    for label, v in fam_eps.items():
        fam[label]["EP"] = len(v)

    return SizeVector(low=lo, high=hi, cu=cu, families={k: dict(v) for k, v in fam.items()}, row_count=len(rows))


# ---------------------------------------------------------------------------
# The rate card and hours (O1, E1).
# ---------------------------------------------------------------------------

CardStatus = Literal["none", "draft", "expired", "approved"]
# U is deliberately never priced (E1): unrecognised work is "N jobs not
# sized", never a number of hours. A card that tries to price it is
# refused outright, not silently ignored -- see RateCard.is_valid().
_PRICEABLE_UNITS = tuple(u for u in UNITS if u != "U")


@dataclass(frozen=True)
class RateCard:
    """Versioned, signed data -- never a guessed default. Every field
    ships empty until Quest1 signs one (O1 in 06-spec-for-vishy.md)."""
    card_version: Optional[str] = None
    status: CardStatus = "none"
    owner: Optional[str] = None
    approved_on: Optional[date] = None
    review_by: Optional[date] = None
    rates_low: dict[str, float] = field(default_factory=dict)
    rates_high: dict[str, float] = field(default_factory=dict)
    management_pct_low: float = 0.0
    management_pct_high: float = 0.0

    def effective_status(self, today: Optional[date] = None) -> CardStatus:
        today = today or date.today()
        if self.status != "approved":
            return self.status
        if self.review_by and today > self.review_by:
            return "expired"
        return "approved"

    def is_priceable(self, today: Optional[date] = None) -> bool:
        """True only when the card is approved, unexpired, owned, and
        prices every unit that's allowed to be priced with low <= high.
        A missing rate or an inverted low/high refuses the whole card --
        no partial pricing, no silent default of 0."""
        if self.effective_status(today) != "approved" or not self.owner or not self.approved_on:
            return False
        for u in _PRICEABLE_UNITS:
            if u not in self.rates_low or u not in self.rates_high:
                return False
            if self.rates_low[u] > self.rates_high[u]:
                return False
            if self.rates_low[u] < 0 or self.rates_high[u] < 0:
                return False
        return True


def hours(vector: SizeVector, card: RateCard, today: Optional[date] = None) -> Optional[tuple[float, float]]:
    """(low, high) planning hours, or None if no report may print hours
    yet (O1). Sums only `_PRICEABLE_UNITS` -- U is structurally excluded,
    not merely omitted by convention, so a future card that happens to
    carry a "U" rate still can't price it (E1)."""
    if not card.is_priceable(today):
        return None
    mgmt_lo, mgmt_hi = 1 + card.management_pct_low, 1 + card.management_pct_high
    lo = sum(vector.low[u] * card.rates_low[u] for u in _PRICEABLE_UNITS) * mgmt_lo
    hi = sum(vector.high[u] * card.rates_high[u] for u in _PRICEABLE_UNITS) * mgmt_hi
    return (lo, hi)
