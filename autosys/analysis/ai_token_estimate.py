"""
Indicative Otto Token Budget — pre-pilot AI-assisted migration planning.

Otto (Astronomer's AI data-engineering agent) and Orbiter (Astronomer's free,
mechanical JIL->Python syntax converter) are the two tools an actual
AutoSys->Astronomer migration would use. This module estimates how much of
Otto's token budget that migration is likely to consume, expressed as a
planning RANGE with an explicit LOW confidence label -- mirroring
effort_model.py's honesty model for person-hours, not competing with it.

This is NOT a cost estimate. Otto's published pricing (Astronomer, as of
Sept 2026) includes an "Otto Intelligence fee priced per million tokens"
plus the pass-through cost of whichever underlying model is used -- neither
the fee rate nor Otto's Labs-phase usage-limit thresholds are publicly
disclosed, so no dollar figure can be derived from this module. See
OTTO_PRICING_NOTE.

Design (see the project plan for full rationale):

  1. Route every job into one of four AI-tooling buckets, using signals
     already on JobAssessment (size, gap_tags, wave_depth) -- no dependency
     on migration_signals / the migration-report endpoint.
  2. For the two Otto-heavy routes (L, XL), cluster jobs by a deterministic
     pattern_key. The first job seen for a pattern pays a full
     PATTERN_DESIGN token band; every subsequent job of the same pattern
     pays a much smaller PATTERN_ADAPTATION band. This is the load-bearing
     idea: a 49-job L tier is not 49 independent redesigns, it's a handful
     of distinct patterns applied repeatedly.
  3. M jobs are assumed to mostly go through Orbiter directly (its own docs
     claim 60-80% automatable coverage on XS/S/M) -- only a documented
     ~30% sample is costed for Otto review, not all of them.
  4. Operational risk / blast radius add a validation-token increment on
     top of whichever base band applies -- more regression risk means more
     review/fix cycles, independent of migration difficulty.

Pure, DB-free, mirrors complexity.py's design so it stays cheap to call for
every job in a bulk `analyze` run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from autosys.analysis.complexity import JobAssessment, WAVE_DEPTH_L_THRESHOLD
from autosys.analysis.gap_analysis import GAP_CATALOGUE

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

ROUTE_ORBITER_FIRST = "ORBITER_FIRST"
ROUTE_ORBITER_WITH_OTTO_REVIEW = "ORBITER_WITH_OTTO_REVIEW"
ROUTE_OTTO_ASSISTED_REDESIGN = "OTTO_ASSISTED_REDESIGN"
ROUTE_ARCHITECTURE_WITH_OTTO = "ARCHITECTURE_WITH_OTTO"

CLUSTERED_ROUTES = frozenset({ROUTE_OTTO_ASSISTED_REDESIGN, ROUTE_ARCHITECTURE_WITH_OTTO})

ROLE_REVIEW = "REVIEW"
ROLE_PATTERN_DESIGN = "PATTERN_DESIGN"
ROLE_PATTERN_ADAPTATION = "PATTERN_ADAPTATION"

_RED_GAP_TAGS = frozenset(
    tag for tag, (severity, _desc) in GAP_CATALOGUE.items() if severity == "RED"
)

CONFIDENCE = "LOW"
CALIBRATION_STATUS = "NOT_CALIBRATED"

OTTO_PRICING_NOTE = (
    "Otto's published pricing model (Astronomer, Sept 2026) includes an "
    '"Otto Intelligence fee" priced per million tokens, PLUS the pass-through '
    "cost of whichever underlying model is used for the session (Anthropic, "
    "OpenAI, or Google -- selectable per session). Specific per-million-token "
    "rates and Labs-phase usage-limit thresholds are not publicly published; "
    "confirm both with Astronomer before using this budget for cost planning."
)

DISCLAIMER = (
    "Indicative Otto Token Budget -- a pre-pilot, order-of-magnitude planning "
    "range, not a usage quote, cost estimate, or contractual commitment. "
    "Replace with observed pilot usage once a Phase 3 pilot has run."
)


@dataclass
class TokenBand:
    min_tokens: int
    max_tokens: int

    def __add__(self, other: "TokenBand") -> "TokenBand":
        return TokenBand(
            self.min_tokens + other.min_tokens, self.max_tokens + other.max_tokens
        )


# ---------------------------------------------------------------------------
# Token bands -- plain, named constants. No YAML/file-based config
# convention exists anywhere in this repo (complexity.py's EFFORT_HOURS and
# effort_model.py's _TIER_HOUR_RANGE are both plain module constants too),
# so this follows the same pattern rather than introducing a new dependency.
# Every band is an order-of-magnitude, expert-judgment estimate -- the same
# epistemic status as the person-hour model before pilot calibration, NOT a
# measurement.
# ---------------------------------------------------------------------------

ORBITER_FIRST_REVIEW_BAND = TokenBand(1_000, 3_000)
ORBITER_OTTO_REVIEW_BAND = TokenBand(8_000, 20_000)

PATTERN_DESIGN_BAND: Dict[str, TokenBand] = {
    ROUTE_OTTO_ASSISTED_REDESIGN: TokenBand(20_000, 60_000),
    ROUTE_ARCHITECTURE_WITH_OTTO: TokenBand(60_000, 200_000),
}
PATTERN_ADAPTATION_BAND: Dict[str, TokenBand] = {
    ROUTE_OTTO_ASSISTED_REDESIGN: TokenBand(3_000, 10_000),
    ROUTE_ARCHITECTURE_WITH_OTTO: TokenBand(8_000, 25_000),
}

# Validation/review increments, layered on top of whichever base band above
# already applies.
VALIDATION_RISK_INCREMENT = TokenBand(2_000, 8_000)
VALIDATION_BLAST_RADIUS_INCREMENT = TokenBand(1_000, 4_000)
BLAST_RADIUS_VALIDATION_THRESHOLD = 5
_RISK_NEEDING_VALIDATION = frozenset({"MEDIUM", "HIGH"})

# Not every M job needs Otto -- Orbiter's own docs claim 60-80% automatable
# coverage on XS/S/M, i.e. 20-40% need review. 0.3 is the midpoint of that
# *documented* range, not an independent guess.
ORBITER_REVIEW_FRACTION = 0.3


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class AITokenEstimate:
    job_name:    str
    route:       str
    role:        str
    pattern_key: str
    min_tokens:  int
    max_tokens:  int
    confidence:  str
    drivers:     str


@dataclass
class AITokenBudgetSummary:
    min_tokens:  int
    max_tokens:  int
    confidence:  str
    calibration_status: str
    pattern_count: int
    architecture_pattern_count: int
    by_route: Dict[str, Dict[str, int]] = field(default_factory=dict)
    by_tier:  Dict[str, Dict[str, int]] = field(default_factory=dict)
    by_job:   Dict[str, AITokenEstimate] = field(default_factory=dict)
    disclaimer: str = DISCLAIMER
    pricing_note: str = OTTO_PRICING_NOTE


# ---------------------------------------------------------------------------
# Routing and clustering
# ---------------------------------------------------------------------------

def _split_gap_tags(gap_tags: str) -> List[str]:
    return [t for t in gap_tags.split(", ") if t]


def route_job(a: JobAssessment) -> str:
    """Assign one of the four AI-tooling routes to a job.

    Deliberately uses only fields already on JobAssessment today (size,
    gap_tags, wave_depth) -- no dependency on migration_signals or the
    /api/v1/assessment/migration-report endpoint, which Shinro's
    tool_execution never calls.
    """
    if a.size == "XL" or any(t in _RED_GAP_TAGS for t in _split_gap_tags(a.gap_tags)):
        return ROUTE_ARCHITECTURE_WITH_OTTO
    if a.size == "L":
        return ROUTE_OTTO_ASSISTED_REDESIGN
    if a.size == "M":
        return ROUTE_ORBITER_WITH_OTTO_REVIEW
    return ROUTE_ORBITER_FIRST


def pattern_key(a: JobAssessment) -> str:
    """Deterministic cluster key for L/XL jobs.

    Deliberately coarse on dependency depth -- a boolean 'deep_chain' flag,
    not the literal wave_depth number. Real AutoSys estates driven mostly by
    cross-box dependency depth (rather than FTP/cross-instance triggers) can
    sit at many different literal depths; a narrow depth-banded key would
    fragment what is really one recurring integration pattern into many
    "patterns," defeating the design-once/adapt-many economics this feature
    exists to surface.
    """
    tags = ",".join(sorted(_split_gap_tags(a.gap_tags)))
    deep_chain = a.wave_depth >= WAVE_DEPTH_L_THRESHOLD
    return f"{a.job_type}|{a.size}|{tags}|deep_chain={deep_chain}"


def _validation_increment(a: JobAssessment) -> TokenBand:
    band = TokenBand(0, 0)
    if a.risk in _RISK_NEEDING_VALIDATION:
        band = band + VALIDATION_RISK_INCREMENT
    if a.blast_radius >= BLAST_RADIUS_VALIDATION_THRESHOLD:
        band = band + VALIDATION_BLAST_RADIUS_INCREMENT
    return band


def _estimate_clustered(
    a: JobAssessment, route: str, seen_patterns: set
) -> AITokenEstimate:
    key = pattern_key(a)
    role = ROLE_PATTERN_DESIGN if key not in seen_patterns else ROLE_PATTERN_ADAPTATION
    seen_patterns.add(key)
    base = (PATTERN_DESIGN_BAND if role == ROLE_PATTERN_DESIGN else PATTERN_ADAPTATION_BAND)[route]
    total = base + _validation_increment(a)
    return AITokenEstimate(
        job_name=a.job_name, route=route, role=role, pattern_key=key,
        min_tokens=total.min_tokens, max_tokens=total.max_tokens,
        confidence=CONFIDENCE,
        drivers=f"{a.size} tier; pattern={key}; role={role}",
    )


def compute_token_budget(results: List[JobAssessment]) -> AITokenBudgetSummary:
    """Compute the Indicative Otto Token Budget for a full assessment run.

    Explicitly NOT job_count x tokens_per_tier: L/XL jobs are clustered by
    pattern_key so repeated instances of the same migration pattern cost a
    small PATTERN_ADAPTATION band, not another full PATTERN_DESIGN band. M
    jobs are assumed to mostly go through Orbiter directly, with only a
    documented ~30% sampled for Otto review tokens.
    """
    seen_patterns: Dict[str, set] = {
        ROUTE_OTTO_ASSISTED_REDESIGN: set(),
        ROUTE_ARCHITECTURE_WITH_OTTO: set(),
    }

    m_jobs = [a for a in results if route_job(a) == ROUTE_ORBITER_WITH_OTTO_REVIEW]
    m_reviewed = {
        a.job_name for a in m_jobs[: round(len(m_jobs) * ORBITER_REVIEW_FRACTION)]
    }

    by_job: Dict[str, AITokenEstimate] = {}

    for a in results:
        route = route_job(a)

        if route in CLUSTERED_ROUTES:
            est = _estimate_clustered(a, route, seen_patterns[route])
        elif route == ROUTE_ORBITER_WITH_OTTO_REVIEW:
            if a.job_name not in m_reviewed:
                # Orbiter handles it directly -- no Otto tokens at all.
                est = AITokenEstimate(
                    job_name=a.job_name, route=route, role=ROLE_REVIEW, pattern_key="",
                    min_tokens=0, max_tokens=0, confidence=CONFIDENCE,
                    drivers="M tier; Orbiter-converted, no Otto involvement",
                )
            else:
                total = ORBITER_OTTO_REVIEW_BAND + _validation_increment(a)
                est = AITokenEstimate(
                    job_name=a.job_name, route=route, role=ROLE_REVIEW, pattern_key="",
                    min_tokens=total.min_tokens, max_tokens=total.max_tokens,
                    confidence=CONFIDENCE,
                    drivers=(
                        f"M tier; Otto review "
                        f"(~{int(ORBITER_REVIEW_FRACTION * 100)}% of M sampled for review)"
                    ),
                )
        else:  # ROUTE_ORBITER_FIRST
            total = ORBITER_FIRST_REVIEW_BAND
            est = AITokenEstimate(
                job_name=a.job_name, route=route, role=ROLE_REVIEW, pattern_key="",
                min_tokens=total.min_tokens, max_tokens=total.max_tokens,
                confidence=CONFIDENCE,
                drivers=f"{a.size} tier; Orbiter handles conversion, spot review only",
            )

        by_job[a.job_name] = est

    by_route: Dict[str, Dict[str, int]] = {}
    by_tier: Dict[str, Dict[str, int]] = {}

    for a in results:
        e = by_job[a.job_name]
        r = by_route.setdefault(e.route, {"jobs": 0, "min_tokens": 0, "max_tokens": 0})
        r["jobs"] += 1
        r["min_tokens"] += e.min_tokens
        r["max_tokens"] += e.max_tokens

        t = by_tier.setdefault(a.size, {"jobs": 0, "min_tokens": 0, "max_tokens": 0})
        t["jobs"] += 1
        t["min_tokens"] += e.min_tokens
        t["max_tokens"] += e.max_tokens

    total_min = sum(e.min_tokens for e in by_job.values())
    total_max = sum(e.max_tokens for e in by_job.values())
    pattern_count = (
        len(seen_patterns[ROUTE_OTTO_ASSISTED_REDESIGN])
        + len(seen_patterns[ROUTE_ARCHITECTURE_WITH_OTTO])
    )

    return AITokenBudgetSummary(
        min_tokens=total_min,
        max_tokens=total_max,
        confidence=CONFIDENCE,
        calibration_status=CALIBRATION_STATUS,
        pattern_count=pattern_count,
        architecture_pattern_count=len(seen_patterns[ROUTE_ARCHITECTURE_WITH_OTTO]),
        by_route=by_route,
        by_tier=by_tier,
        by_job=by_job,
    )
