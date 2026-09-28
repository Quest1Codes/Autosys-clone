"""Tests for the Indicative Otto Token Budget (ai_token_estimate.py).

Pure-function tests, no DB needed -- mirrors test_assessment.py's
TestGapAnalysis/TestOperationalRisk style of constructing bare
JobAssessment/JobRow objects directly rather than going through the DB.
"""
from __future__ import annotations

from autosys.analysis.complexity import JobAssessment, EFFORT_HOURS
from autosys.analysis.ai_token_estimate import (
    ROUTE_ORBITER_FIRST,
    ROUTE_ORBITER_WITH_OTTO_REVIEW,
    ROUTE_OTTO_ASSISTED_REDESIGN,
    ROUTE_ARCHITECTURE_WITH_OTTO,
    ROLE_PATTERN_DESIGN,
    ROLE_PATTERN_ADAPTATION,
    ORBITER_FIRST_REVIEW_BAND,
    PATTERN_DESIGN_BAND,
    PATTERN_ADAPTATION_BAND,
    VALIDATION_RISK_INCREMENT,
    VALIDATION_BLAST_RADIUS_INCREMENT,
    BLAST_RADIUS_VALIDATION_THRESHOLD,
    route_job,
    pattern_key,
    compute_token_budget,
)


def _job(name, size, job_type="CMD", gap_tags="", risk="NONE", blast_radius=0, wave_depth=1):
    return JobAssessment(
        job_name=name, job_type=job_type, box_name="", size=size,
        effort_h=EFFORT_HOURS[size], drivers="",
        risk=risk, blast_radius=blast_radius, gap_tags=gap_tags,
        wave_depth=wave_depth,
    )


class TestRouting:
    def test_xs_s_route_to_orbiter_first(self):
        assert route_job(_job("a", "XS")) == ROUTE_ORBITER_FIRST
        assert route_job(_job("b", "S")) == ROUTE_ORBITER_FIRST

    def test_m_routes_to_orbiter_with_otto_review(self):
        assert route_job(_job("c", "M")) == ROUTE_ORBITER_WITH_OTTO_REVIEW

    def test_l_routes_to_otto_assisted_redesign(self):
        assert route_job(_job("d", "L")) == ROUTE_OTTO_ASSISTED_REDESIGN

    def test_xl_routes_to_architecture_with_otto(self):
        assert route_job(_job("e", "XL")) == ROUTE_ARCHITECTURE_WITH_OTTO

    def test_red_gap_tag_forces_architecture_route_even_off_tier(self):
        # An S-tier job with a RED gap tag (shouldn't structurally happen from
        # score_job today, but the router must not silently under-route a
        # job that gap_analysis has already flagged as needing a paradigm
        # shift) still escalates to ARCHITECTURE_WITH_OTTO.
        j = _job("f", "S", gap_tags="cross-instance-event")
        assert route_job(j) == ROUTE_ARCHITECTURE_WITH_OTTO


class TestOrbiterFirstBand:
    def test_xs_s_get_small_review_band_only(self):
        budget = compute_token_budget([_job("a", "XS"), _job("b", "S")])
        for name in ("a", "b"):
            est = budget.by_job[name]
            assert est.min_tokens == ORBITER_FIRST_REVIEW_BAND.min_tokens
            assert est.max_tokens == ORBITER_FIRST_REVIEW_BAND.max_tokens
            assert est.min_tokens < PATTERN_DESIGN_BAND[ROUTE_OTTO_ASSISTED_REDESIGN].min_tokens


class TestPatternClustering:
    def test_two_identical_xl_jobs_share_one_design_plus_one_adaptation(self):
        j1 = _job("xl1", "XL", job_type="BOX", gap_tags="complex-calendar", wave_depth=8)
        j2 = _job("xl2", "XL", job_type="BOX", gap_tags="complex-calendar", wave_depth=9)
        budget = compute_token_budget([j1, j2])

        assert pattern_key(j1) == pattern_key(j2)  # same cluster
        roles = {budget.by_job["xl1"].role, budget.by_job["xl2"].role}
        assert roles == {ROLE_PATTERN_DESIGN, ROLE_PATTERN_ADAPTATION}

        design_band = PATTERN_DESIGN_BAND[ROUTE_ARCHITECTURE_WITH_OTTO]
        adaptation_band = PATTERN_ADAPTATION_BAND[ROUTE_ARCHITECTURE_WITH_OTTO]
        design_est = next(e for e in (budget.by_job["xl1"], budget.by_job["xl2"]) if e.role == ROLE_PATTERN_DESIGN)
        adaptation_est = next(e for e in (budget.by_job["xl1"], budget.by_job["xl2"]) if e.role == ROLE_PATTERN_ADAPTATION)
        assert design_est.min_tokens == design_band.min_tokens
        assert adaptation_est.min_tokens == adaptation_band.min_tokens
        assert adaptation_est.max_tokens < design_est.max_tokens

    def test_different_gap_tags_produce_different_patterns(self):
        j1 = _job("xl1", "XL", job_type="BOX", gap_tags="complex-calendar", wave_depth=8)
        j2 = _job("xl2", "XL", job_type="BOX", gap_tags="sla-management", wave_depth=8)
        assert pattern_key(j1) != pattern_key(j2)
        budget = compute_token_budget([j1, j2])
        # Both are first-of-their-own pattern -> both PATTERN_DESIGN
        assert budget.by_job["xl1"].role == ROLE_PATTERN_DESIGN
        assert budget.by_job["xl2"].role == ROLE_PATTERN_DESIGN
        assert budget.pattern_count == 2

    def test_l_and_xl_clusters_are_independent(self):
        j_l = _job("l1", "L", job_type="CMD", wave_depth=5)
        j_xl = _job("xl1", "XL", job_type="BOX", wave_depth=8)
        budget = compute_token_budget([j_l, j_xl])
        assert budget.by_job["l1"].role == ROLE_PATTERN_DESIGN
        assert budget.by_job["xl1"].role == ROLE_PATTERN_DESIGN
        assert budget.pattern_count == 2
        assert budget.architecture_pattern_count == 1


class TestValidationIncrement:
    def test_high_risk_adds_validation_tokens_without_changing_base_tier(self):
        low_risk = _job("a", "L", job_type="CMD", risk="NONE", wave_depth=5)
        high_risk = _job("b", "L", job_type="CMD", risk="HIGH", wave_depth=5)
        # Different pattern_key isn't affected by risk, but different job
        # names mean the second job seen for this pattern -> ADAPTATION.
        # Force both to be first-of-kind by scoring them independently.
        budget_low = compute_token_budget([low_risk])
        budget_high = compute_token_budget([high_risk])

        est_low = budget_low.by_job["a"]
        est_high = budget_high.by_job["b"]
        assert est_low.role == est_high.role == ROLE_PATTERN_DESIGN
        assert est_high.min_tokens == est_low.min_tokens + VALIDATION_RISK_INCREMENT.min_tokens
        assert est_high.max_tokens == est_low.max_tokens + VALIDATION_RISK_INCREMENT.max_tokens

    def test_high_blast_radius_adds_validation_tokens(self):
        low_blast = _job("a", "L", job_type="CMD", blast_radius=1, wave_depth=5)
        high_blast = _job(
            "b", "L", job_type="CMD",
            blast_radius=BLAST_RADIUS_VALIDATION_THRESHOLD, wave_depth=5,
        )
        budget_low = compute_token_budget([low_blast])
        budget_high = compute_token_budget([high_blast])

        est_low = budget_low.by_job["a"]
        est_high = budget_high.by_job["b"]
        assert est_high.min_tokens == est_low.min_tokens + VALIDATION_BLAST_RADIUS_INCREMENT.min_tokens


class TestMReviewFraction:
    def test_only_a_documented_fraction_of_m_jobs_get_otto_tokens(self):
        m_jobs = [_job(f"m{i}", "M") for i in range(10)]
        budget = compute_token_budget(m_jobs)
        reviewed = [e for e in budget.by_job.values() if e.max_tokens > 0]
        unreviewed = [e for e in budget.by_job.values() if e.max_tokens == 0]
        # ORBITER_REVIEW_FRACTION = 0.3 -> round(10 * 0.3) = 3
        assert len(reviewed) == 3
        assert len(unreviewed) == 7


class TestSummaryAggregation:
    def test_summary_totals_match_sum_of_per_job_estimates(self):
        jobs = [
            _job("xs1", "XS"), _job("s1", "S"),
            _job("m1", "M"), _job("m2", "M"), _job("m3", "M"),
            _job("l1", "L", job_type="CMD", wave_depth=5),
            _job("xl1", "XL", job_type="BOX", wave_depth=8),
        ]
        budget = compute_token_budget(jobs)
        assert budget.min_tokens == sum(e.min_tokens for e in budget.by_job.values())
        assert budget.max_tokens == sum(e.max_tokens for e in budget.by_job.values())
        assert budget.confidence == "LOW"
        assert budget.calibration_status == "NOT_CALIBRATED"

        assert sum(v["jobs"] for v in budget.by_tier.values()) == len(jobs)
        assert sum(v["jobs"] for v in budget.by_route.values()) == len(jobs)
