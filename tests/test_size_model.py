"""
T17: the 13-unit size model and rate card -- the direct replacement for
the single effort number Vishy called "blind and random".
"""
from __future__ import annotations

import datetime
import random

import pytest

from autosys.analysis.size_model import (
    RateCard, UNITS, arg_shape, constructs, exe_template, flow_roots,
    hours, platform_blocks, segments, size,
)
from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo
from autosys.db.schema import JobRow
from autosys.models.job import parse_job


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_size_model.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection as _conn
    _conn.reset_engines()
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=False)
    yield
    _conn.reset_engines()


def _reseed(jobs: list[dict]):
    with sync_session() as s:
        s.query(JobRow).delete()
        s.commit()
    with sync_session() as s:
        for j in jobs:
            job_repo.upsert(s, parse_job(j))
        s.commit()


def _rows():
    with sync_session() as s:
        return job_repo.list_all(s)


GOOD_CARD = dict(card_version="t", status="approved", owner="quest1-lead",
                  approved_on=datetime.date(2026, 1, 1), review_by=datetime.date(2027, 1, 1))
TODAY = datetime.date(2026, 11, 6)


def _flat_card(rate: float = 1.0) -> RateCard:
    return RateCard(**GOOD_CARD, rates_low={u: rate for u in UNITS}, rates_high={u: rate for u in UNITS})


class TestSegments:
    """segments() returns each statement's own text (for pattern/arg-shape
    purposes downstream), not a reduced executable name -- that's a
    different job, done by classification.executable()."""

    def test_splits_on_statement_separators(self):
        assert segments(". /etc/profile; /app/bin/load.sh") == ["/app/bin/load.sh"]
        assert segments("cmd1 && cmd2") == ["cmd1", "cmd2"]

    def test_unwraps_bare_wrapper_names(self):
        assert segments("env /app/bin/x.sh") == ["/app/bin/x.sh"]
        assert segments("env FOO=bar /app/bin/x.sh") == ["/app/bin/x.sh"]

    def test_sudo_dash_u_value_is_not_stripped_here(self):
        # A real, documented limitation (not silently guessed either way):
        # this function only strips a flag TOKEN, never a separate value
        # token after it, so "-u svc" leaves "svc" in the result.
        # classification.executable() (used for classifying/disposing a
        # job) does handle this correctly; this function, used only for
        # sizing's pattern/arg-shape keys, does not.
        assert segments("sudo -u svc /app/bin/x.sh") == ["svc /app/bin/x.sh"]

    def test_empty_command(self):
        assert segments(None) == []
        assert segments("") == []


class TestExeTemplateAndArgShape:
    def test_folds_digits_and_variables(self):
        assert exe_template("/opt/app/daily_load_42.ksh -d $$BATCH_DATE") == "daily_load_#.ksh"

    def test_arg_shape_flags_vs_values(self):
        assert arg_shape("/opt/app/run.sh -d today x.txt") == "faa"
        assert arg_shape("/opt/app/run.sh --verbose -d today") == "ffa"


class TestConstructs:
    def test_detects_or_and_lookback_and_exit_code(self):
        class Row:
            condition = "s(a) | e(b,12) = 0"
            box_success = box_failure = box_terminator = job_terminator = resources = None
            command = std_out_file = std_err_file = std_in_file = None
        hits = constructs(Row())
        assert "cond_or" in hits
        assert "exit_code" in hits

    def test_detects_global_var_in_command(self):
        class Row:
            condition = None
            box_success = box_failure = box_terminator = job_terminator = resources = None
            command = "/opt/app/run.sh $$BATCH_DATE"
            std_out_file = std_err_file = std_in_file = None
        assert "global_var" in constructs(Row())


class TestFlowRoots:
    def test_simple_chain(self):
        class Row:
            def __init__(self, name, box):
                self.job_name, self.box_name = name, box
        rows = [Row("box1", None), Row("a", "box1"), Row("b", "box1")]
        roots = flow_roots(rows)
        assert roots["a"] == roots["b"] == roots["box1"] == "box1"

    def test_box_loop_does_not_infinite_loop(self):
        class Row:
            def __init__(self, name, box):
                self.job_name, self.box_name = name, box
        # A malformed export: box1's parent is box2, box2's parent is box1.
        rows = [Row("box1", "box2"), Row("box2", "box1")]
        roots = flow_roots(rows)
        assert roots["box1"] == roots["box2"]  # resolves to a single, stable root, not an infinite loop


class TestPlatformBlocks:
    def test_oss_blocks_within_documented_range(self):
        lo, hi = platform_blocks("oss", {})
        assert 0 <= lo <= hi <= 13

    def test_astro_blocks_within_documented_range(self):
        lo, hi = platform_blocks("astro", {})
        assert 0 <= lo <= hi <= 9

    def test_network_yes_removes_the_network_block(self):
        lo_unsure, hi_unsure = platform_blocks("oss", {"network_reachability": "Unsure"})
        lo_yes, hi_yes = platform_blocks("oss", {"network_reachability": "Yes — reachable"})
        assert hi_yes < hi_unsure


class TestSizeBasics:
    def test_box_is_not_its_own_instance(self):
        _reseed([
            {"job_name": "box1", "job_type": "BOX"},
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo hi", "machine": "m1", "box_name": "box1"},
        ])
        v = size(_rows())
        assert v.low["I"] == 1  # only "a", never "box1" too

    def test_unknown_job_counts_toward_u(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/opt/frobco/bin/weird", "machine": "m1"}])
        v = size(_rows())
        assert v.excess_unknown_jobs() == 1

    def test_cu_is_not_in_the_units_vector(self):
        assert "CU" not in UNITS

    def test_same_input_gives_same_output(self):
        jobs = [{"job_name": f"j{i}", "job_type": "CMD", "command": f"/opt/app/run_{i}.sh", "machine": "m1"} for i in range(10)]
        _reseed(jobs)
        v1 = size(_rows())
        v2 = size(_rows())
        assert v1.low == v2.low and v1.high == v2.high and v1.cu == v2.cu

    def test_row_order_does_not_change_the_result(self):
        jobs = [{"job_name": f"j{i}", "job_type": "CMD", "command": f"/opt/app/run_{i}.sh", "machine": "m1",
                  "condition": f"s(j{i - 1})" if i else None} for i in range(10)]
        _reseed(jobs)
        v_forward = size(_rows())
        _reseed(list(reversed(jobs)))
        v_reversed = size(_rows())
        assert v_forward.low == v_reversed.low and v_forward.high == v_reversed.high


class TestSizeVectorConstructBreakdown:
    """T32 (S6): per-construct-type job counts, so the report can say
    *which* behaviour changes, not just how many decisions exist in
    total (size.E)."""

    def test_breaks_down_by_construct_type(self):
        _reseed([
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1",
             "condition": "s(x) | e(y) = 0"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1",
             "condition": "e(z) = 0"},
        ])
        v = size(_rows())
        assert v.constructs["cond_or"] == 1
        assert v.constructs["exit_code"] == 2  # both jobs have an exit-code condition
        assert "look_back" not in v.constructs

    def test_sums_to_e_when_no_credential_shapes_present(self):
        # E counts every hit, including "credential" when redact_and_flag
        # fires; with no credential-shaped command, constructs' own hits
        # (not "credential") must sum to exactly size.E across the estate.
        _reseed([
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1",
             "condition": "s(x) | e(y,12) = 0"},
        ])
        v = size(_rows())
        assert sum(v.constructs.values()) == v.low["E"] == v.high["E"]

    def test_empty_estate_gives_an_empty_breakdown_not_a_crash(self):
        _reseed([])
        v = size(_rows())
        assert v.constructs == {}


class TestCutoverGroupSizes:
    """T32 (S9): job count per cutover group, largest first -- the
    ranked list a "top N groups" table needs, distinct from cu's own
    single largest_jobs/largest_flows numbers."""

    def test_sorted_largest_first(self):
        jobs = [
            # Group 1: a -> b -> c (3 jobs)
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1", "condition": "s(a)"},
            {"job_name": "c", "job_type": "CMD", "command": "/bin/echo c", "machine": "m1", "condition": "s(b)"},
            # Group 2: standalone (1 job)
            {"job_name": "d", "job_type": "CMD", "command": "/bin/echo d", "machine": "m1"},
            # Group 3: e -> f (2 jobs)
            {"job_name": "e", "job_type": "CMD", "command": "/bin/echo e", "machine": "m1"},
            {"job_name": "f", "job_type": "CMD", "command": "/bin/echo f", "machine": "m1", "condition": "s(e)"},
        ]
        _reseed(jobs)
        v = size(_rows())
        assert v.cutover_group_sizes == (3, 2, 1)

    def test_largest_matches_cus_own_figure(self):
        jobs = [
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1", "condition": "s(a)"},
        ]
        _reseed(jobs)
        v = size(_rows())
        assert v.cutover_group_sizes[0] == v.cu["largest_jobs"]

    def test_empty_estate_gives_an_empty_tuple(self):
        _reseed([])
        v = size(_rows())
        assert v.cutover_group_sizes == ()


class TestRateCardGating:
    """O1: no hours print until a signed, unexpired, fully-priced card exists."""

    def test_no_card_gives_no_hours(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"}])
        assert hours(size(_rows()), RateCard()) is None

    def test_draft_card_gives_no_hours(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"}])
        draft = RateCard(**{**GOOD_CARD, "status": "draft"}, rates_low={u: 1.0 for u in UNITS}, rates_high={u: 1.0 for u in UNITS})
        assert hours(size(_rows()), draft) is None

    def test_expired_card_gives_no_hours(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"}])
        expired = RateCard(card_version="t", status="approved", owner="x",
                            approved_on=datetime.date(2025, 1, 1), review_by=datetime.date(2025, 6, 1),
                            rates_low={u: 1.0 for u in UNITS}, rates_high={u: 1.0 for u in UNITS})
        assert expired.effective_status(TODAY) == "expired"
        assert hours(size(_rows()), expired, TODAY) is None

    def test_card_missing_a_priceable_rate_is_refused_entirely(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"}])
        missing_b = RateCard(**GOOD_CARD, rates_low={u: 1.0 for u in UNITS if u != "B"},
                              rates_high={u: 1.0 for u in UNITS if u != "B"})
        assert hours(size(_rows()), missing_b, TODAY) is None

    def test_inverted_low_high_is_refused(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"}])
        inverted = RateCard(**GOOD_CARD, rates_low={u: 5.0 for u in UNITS}, rates_high={u: 1.0 for u in UNITS})
        assert hours(size(_rows()), inverted, TODAY) is None

    def test_negative_rate_is_refused(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"}])
        negative = RateCard(**GOOD_CARD, rates_low={**{u: 1.0 for u in UNITS}, "I": -1.0},
                             rates_high={u: 1.0 for u in UNITS})
        assert hours(size(_rows()), negative, TODAY) is None

    def test_approved_full_card_gives_hours(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"}])
        lo, hi = hours(size(_rows()), _flat_card(), TODAY)
        assert lo > 0 and hi >= lo


class TestE1UnknownJobsNeverPriced:
    """E1 (05-vishy-decisions.md): unknown jobs must never be priced,
    structurally -- not by trusting every card to leave "U" unpriced."""

    def test_u_rate_has_zero_effect_on_hours_at_any_value(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"},
                  {"job_name": "z", "job_type": "CMD", "command": "/opt/frobco/bin/weird", "machine": "m1"}])
        v = size(_rows())
        assert v.excess_unknown_jobs() == 1
        card_low_u = RateCard(**GOOD_CARD, rates_low={u: 1.0 for u in UNITS}, rates_high={u: 1.0 for u in UNITS})
        card_high_u = RateCard(**GOOD_CARD, rates_low={**{u: 1.0 for u in UNITS}, "U": 999_999.0},
                                rates_high={**{u: 1.0 for u in UNITS}, "U": 999_999.0})
        assert hours(v, card_low_u, TODAY) == hours(v, card_high_u, TODAY)


class TestDominantOrder:
    """E2: a full ranking by one guessed rate card held on only 6 of 36
    real pairs. Only a dominance relation (bigger on every unit, strictly
    bigger on one) holds at any rate card."""

    def test_family_with_strictly_more_of_everything_dominates(self):
        _reseed([
            {"job_name": "sap1", "job_type": "SAP", "machine": "m1"},
            {"job_name": "sap2", "job_type": "SAP", "machine": "m2"},
            {"job_name": "pmcmd1", "job_type": "CMD", "command": "pmcmd startworkflow wf1", "machine": "m1"},
        ])
        v = size(_rows())
        assert ("sap", "informatica") in v.dominant_order()
        assert ("informatica", "sap") not in v.dominant_order()

    def test_incomparable_families_produce_no_pair_either_way(self):
        # sap: 1 job, but with an OR condition (an E construct) -- ahead
        # on E. informatica: 2 jobs on 2 machines, no OR -- ahead on I,
        # EP and R_hi. Neither is >= the other on every unit, so there
        # must be no pair between them in either direction.
        _reseed([
            {"job_name": "x", "job_type": "CMD", "command": "/bin/echo hi", "machine": "m1"},
            {"job_name": "sap1", "job_type": "SAP", "machine": "m1", "condition": "s(x)|f(x)"},
            {"job_name": "pmcmd1", "job_type": "CMD", "command": "pmcmd startworkflow wf1", "machine": "m1"},
            {"job_name": "pmcmd2", "job_type": "CMD", "command": "pmcmd startworkflow wf2", "machine": "m2"},
        ])
        v = size(_rows())
        order = v.dominant_order()
        assert ("sap", "informatica") not in order
        assert ("informatica", "sap") not in order

    def test_identical_families_produce_no_pair(self):
        _reseed([{"job_name": "sap1", "job_type": "SAP", "machine": "m1"}])
        v = size(_rows())
        # Only one family exists, so there's nothing to compare -- no
        # pair should ever contain a family paired with itself.
        assert all(a != b for a, b in v.dominant_order())


class TestDependencyRemovalCanOnlyWorsenOrHoldSize:
    """The property SWE-2's rejected model failed (27 of 200 trials,
    05-vishy-decisions.md): removing a dependency can never raise a unit
    count. 60 trials here, not 200 -- enough to catch a real regression
    without slowing every test run, same reasoning the research itself
    gave for its own smaller standing suites vs. one-off deep checks."""

    def test_removing_a_condition_never_raises_any_unit(self):
        rnd = random.Random(42)
        trials = 0
        for _ in range(60):
            n = rnd.randint(3, 15)
            names = [f"j{i}" for i in range(n)]
            jobs = []
            for i, nm in enumerate(names):
                cond = f"s({rnd.choice(names[:i])})" if i > 0 and rnd.random() < 0.6 else None
                jobs.append({"job_name": nm, "job_type": "CMD", "command": f"/opt/app/run_{i}.sh",
                             "machine": "m1", "condition": cond})
            _reseed(jobs)
            v_before = size(_rows())
            have_cond = [j for j in jobs if j.get("condition")]
            if not have_cond:
                continue
            victim = rnd.choice(have_cond)["job_name"]
            jobs2 = [dict(j) for j in jobs]
            for j in jobs2:
                if j["job_name"] == victim:
                    j["condition"] = None
            _reseed(jobs2)
            v_after = size(_rows())
            trials += 1
            for u in UNITS:
                assert v_after.high[u] <= v_before.high[u], f"unit {u} rose after removing a dependency"
        assert trials > 30  # make sure the random generator actually exercised enough cases with a condition
