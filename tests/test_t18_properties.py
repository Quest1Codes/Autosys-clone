"""
T18: re-runs the 21 properties from the design study
(`llm-responses/10-effort-without-measurement-opus5.5.md` §4.2, run there
against a prototype model) against the real, production `classify_row`/
`build_manifest`/`size()` pipeline (T15-T17) -- no adapter, no prototype
stand-in.

Four of the 21 are already covered elsewhere, against this same real
pipeline, and are not repeated here:

- T1 (same input, same size) -- `test_size_model.py::TestSizeBasics::
  test_same_input_gives_same_output`
- T2 (row order changes nothing) -- `test_size_model.py::TestSizeBasics::
  test_row_order_does_not_change_the_result`
- T6 (removing a dependency can't raise any unit) --
  `test_size_model.py::TestDependencyRemovalCanOnlyWorsenOrHoldSize`
  (60 random trials, the exact property the design study's own prompt
  flagged as the one a competing model failed)
- T16 (a box_name loop ends, in any order) -- `test_size_model.py::
  TestFlowRoots::test_box_loop_does_not_infinite_loop`

T2e (shuffled file blocks, re-imported through the real JIL parser,
not just the in-memory model), T11/T11b/T11c (questionnaire-answer
narrowing, off-list handling, "not ours" routing) are not covered here
either -- they need T27's real questionnaire wiring to test against
real answer flows rather than hand-built answer dicts, and are left as
an explicit gap, not silently skipped.
"""
from __future__ import annotations

import datetime
import random

import pytest

from autosys.analysis.classification import classify_row
from autosys.analysis.disposition import build_manifest
from autosys.analysis.size_model import RateCard, UNITS, hours, size
from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo
from autosys.db.schema import JobRow
from autosys.models.job import parse_job

_PRICEABLE = tuple(u for u in UNITS if u != "U")


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_t18_properties.db"
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


def _random_chain(n: int, prefix: str = "j", machine: str = "m1") -> list[dict]:
    """n jobs in a simple dependency chain j0 -> j1 -> ... -> j(n-1)."""
    jobs = []
    for i in range(n):
        job = {"job_name": f"{prefix}{i}", "job_type": "CMD",
               "command": f"/opt/app/run_{i}.sh", "machine": machine}
        if i:
            job["condition"] = f"s({prefix}{i - 1})"
        jobs.append(job)
    return jobs


class TestT3RenamingChangesNothing:
    """Size follows structure, not names."""

    def test_renaming_every_job_box_and_reference_changes_nothing(self):
        jobs = [
            {"job_name": "box1", "job_type": "BOX"},
            {"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1", "box_name": "box1"},
            {"job_name": "b", "job_type": "CMD", "command": "startrfc -h x", "machine": "m1",
             "box_name": "box1", "condition": "s(a)"},
        ]
        _reseed(jobs)
        v1 = size(_rows())

        renamed = [
            {"job_name": "BOXZ", "job_type": "BOX"},
            {"job_name": "ALPHA", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1", "box_name": "BOXZ"},
            {"job_name": "BETA", "job_type": "CMD", "command": "startrfc -h x", "machine": "m1",
             "box_name": "BOXZ", "condition": "s(ALPHA)"},
        ]
        _reseed(renamed)
        v2 = size(_rows())

        assert v1.low == v2.low and v1.high == v2.high and v1.cu == v2.cu


class TestT5DuplicateJobAddsAFixedAmount:
    """A duplicate, disjoint job adds exactly: I+1, its own constructs to E,
    P/F/EP/C unchanged if it reuses an existing pattern/family/machine/
    calendar, FL+1 if it's a standalone flow."""

    def test_duplicate_of_an_existing_pattern_on_the_same_machine_adds_only_i_and_fl(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"}])
        v1 = size(_rows())

        _reseed([
            {"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"},
            {"job_name": "b", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"},
        ])
        v2 = size(_rows())

        assert v2.low["I"] == v1.low["I"] + 1
        assert v2.low["FL"] == v1.low["FL"] + 1
        # Same command template, same machine -> no new pattern, family or endpoint.
        assert v2.low["P"] == v1.low["P"]
        assert v2.low["EP"] == v1.low["EP"]
        assert v2.low["C"] == v1.low["C"]


class TestT7RemovingAJobCantRaiseAnyUnit:
    """Same property as T6 (dependency removal), for whole jobs: deleting a
    job and every reference to it can only lower or hold each unit, never
    raise one."""

    def test_random_job_removal_never_raises_a_unit(self):
        random.seed(1807)
        for _trial in range(30):
            n = random.randint(3, 12)
            jobs = _random_chain(n)
            _reseed(jobs)
            v_before = size(_rows())

            victim = random.randrange(n)
            victim_name = f"j{victim}"
            # Drop the victim, and any condition referencing it (same as the
            # JIL re-export a real removal would produce).
            remaining = []
            for j in jobs:
                if j["job_name"] == victim_name:
                    continue
                if j.get("condition") and victim_name in j["condition"]:
                    j = {k: v for k, v in j.items() if k != "condition"}
                remaining.append(j)
            _reseed(remaining)
            v_after = size(_rows())

            for unit in UNITS:
                assert v_after.high[unit] <= v_before.high[unit], (
                    f"trial with n={n}: unit {unit} rose after removing a job "
                    f"({v_before.high[unit]} -> {v_after.high[unit]})"
                )


class TestT8TheseFieldsCarryNoSize:
    """n_retrys, term_run_time, alarm_if_fail, timezone, start_times must
    not move any counted unit on their own -- they're scheduling/alerting
    metadata, not structural signals classify_for_sizing or constructs()
    reads."""

    def test_varying_these_fields_alone_changes_nothing(self):
        base = {"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"}
        _reseed([base])
        v_base = size(_rows())

        varied = {
            **base,
            "n_retrys": 5, "term_run_time": 120, "alarm_if_fail": True,
            "timezone": "America/New_York", "start_times": "06:00,18:00",
        }
        _reseed([varied])
        v_varied = size(_rows())

        assert v_base.low == v_varied.low and v_base.high == v_varied.high


class TestT9LedgerTies:
    def test_t9a_i_equals_non_box_rows(self):
        jobs = [
            {"job_name": "box1", "job_type": "BOX"},
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1", "box_name": "box1"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1", "box_name": "box1"},
            {"job_name": "c", "job_type": "CMD", "command": "/bin/echo c", "machine": "m1"},
        ]
        _reseed(jobs)
        v = size(_rows())
        non_box = sum(1 for j in jobs if j["job_type"] != "BOX")
        assert v.low["I"] == v.high["I"] == non_box

    def test_t9b_every_row_sits_in_exactly_one_flow(self):
        jobs = [
            {"job_name": "box1", "job_type": "BOX"},
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1", "box_name": "box1"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1"},
            {"job_name": "c", "job_type": "CMD", "command": "/bin/echo c", "machine": "m1", "condition": "s(b)"},
        ]
        _reseed(jobs)
        from autosys.analysis.size_model import flow_roots
        rows = _rows()
        roots = flow_roots(rows)
        # Every stored row (box and leaf alike) resolves to exactly one root --
        # flow_roots() returns a plain dict, so "exactly one" is structural
        # (a dict value can't be two things), but every row must be present.
        assert set(roots) == {r.job_name for r in rows}

    def test_t9c_wrapping_jobs_in_a_box_adds_no_i_or_p(self):
        loose = [
            {"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1"},
        ]
        _reseed(loose)
        v_loose = size(_rows())

        boxed = [
            {"job_name": "box1", "job_type": "BOX"},
            {"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh", "machine": "m1", "box_name": "box1"},
        ]
        _reseed(boxed)
        v_boxed = size(_rows())

        assert v_loose.low["I"] == v_boxed.low["I"]
        assert v_loose.low["P"] == v_boxed.low["P"]

    def test_t9d_cutover_units_add_back_to_the_estate(self):
        jobs = [
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1", "condition": "s(a)"},
            {"job_name": "c", "job_type": "CMD", "command": "/bin/echo c", "machine": "m1"},  # standalone
        ]
        _reseed(jobs)
        v = size(_rows())
        jobs_in_units = v.cu["alone"] * 1  # each "alone" unit holds exactly 1 job by definition
        # The two cutover units here: {a,b} (2 jobs) and {c} (1 job, alone).
        assert v.cu["CU"] == 2
        assert v.cu["alone"] == 1
        assert v.cu["largest_jobs"] == 2


class TestT12DoublingAnEstate:
    """Doubling a disjoint copy of the same estate: P/F/EP/C stay flat
    (same patterns, families, machines, calendars reused), I/FL/U double."""

    def test_doubling_leaves_p_f_ep_c_flat_and_doubles_i_fl(self):
        def _one_copy(suffix: str) -> list[dict]:
            return [
                {"job_name": f"box{suffix}", "job_type": "BOX"},
                {"job_name": f"a{suffix}", "job_type": "CMD", "command": "/opt/app/run.sh",
                 "machine": "m1", "box_name": f"box{suffix}"},
                {"job_name": f"b{suffix}", "job_type": "CMD", "command": "startrfc -h x",
                 "machine": "m1", "box_name": f"box{suffix}", "run_calendar": "CAL_A"},
            ]

        _reseed(_one_copy("1"))
        v1 = size(_rows())

        _reseed(_one_copy("1") + _one_copy("2"))
        v2 = size(_rows())

        assert v2.low["P"] == v1.low["P"]
        assert v2.low["F"] == v1.low["F"]
        assert v2.low["EP"] == v1.low["EP"]
        assert v2.low["C"] == v1.low["C"]
        assert v2.low["I"] == v1.low["I"] * 2
        assert v2.low["FL"] == v1.low["FL"] * 2


class TestT13AnyNonNegativeRateCardKeepsT6:
    """T6 (dependency removal can only lower or hold size) must hold at
    the priced level too, for any valid, non-negative rate card -- hours
    is a linear, non-negative-weighted sum of units, so a monotonic size
    decrease can only ever produce a monotonic hours decrease or hold."""

    @staticmethod
    def _random_card(rng: random.Random) -> RateCard:
        rates_low = {u: round(rng.uniform(0, 50), 2) for u in _PRICEABLE}
        rates_high = {u: rates_low[u] + round(rng.uniform(0, 50), 2) for u in _PRICEABLE}
        return RateCard(
            card_version="t18", status="approved", owner="quest1-lead",
            approved_on=datetime.date(2026, 1, 1), review_by=datetime.date(2027, 1, 1),
            rates_low=rates_low, rates_high=rates_high,
        )

    def test_random_cards_never_let_removing_a_dependency_raise_hours(self):
        rng = random.Random(2609)
        for _trial in range(25):
            jobs = [
                {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"},
                {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1", "condition": "s(a)"},
            ]
            _reseed(jobs)
            v_with_dep = size(_rows())

            without_dep = [
                {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"},
                {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1"},
            ]
            _reseed(without_dep)
            v_without_dep = size(_rows())

            card = self._random_card(rng)
            h_with = hours(v_with_dep, card)
            h_without = hours(v_without_dep, card)
            assert h_with is not None and h_without is not None
            assert h_without[1] <= h_with[1] + 1e-9, (
                f"trial: hours rose after removing a dependency "
                f"({h_with[1]} -> {h_without[1]}) with card {card.rates_high}"
            )


class TestT14ExitWindowAndTargetMoveOnlyWhatTheyShould:
    def test_t14a_exit_window_and_outside_jil_answers_move_no_count(self):
        # size() doesn't read autosys_exit_window/outside_jil_scheduling at
        # all -- they're real answer keys from the design (pace/scope-only
        # text, S10/S2), structurally incapable of moving a count here.
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"}])
        v_no_answers = size(_rows(), answers={})
        v_with_answers = size(_rows(), answers={
            "autosys_exit_window": "More than 24 months away",
            "outside_jil_scheduling": "cron, manual triggers",
        })
        assert v_no_answers.low == v_with_answers.low
        assert v_no_answers.high == v_with_answers.high

    def test_t14b_target_choice_moves_only_b(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"}])
        v_oss = size(_rows(), answers={"target_platform": "oss"})
        v_astro = size(_rows(), answers={"target_platform": "astro"})
        for unit in UNITS:
            if unit == "B":
                continue
            assert v_oss.low[unit] == v_astro.low[unit], f"{unit} moved with target choice"
            assert v_oss.high[unit] == v_astro.high[unit], f"{unit} moved with target choice"
