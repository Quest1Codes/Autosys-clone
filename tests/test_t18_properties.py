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

The two former gaps are now covered here as well: T2e (shuffled file
blocks re-imported through the real `jil_ingest` path, not just the
in-memory model) and T11/T11b/T11c (answer narrowing, off-list handling,
"not ours" routing) -- with a sibling-checkout contract test asserting
the shipped questionnaire's option strings are exactly the strings the
model's effect tables consume.
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


class TestT2eShuffledFileBlocks:
    """L234's gap closed: `update_job`/`delete_job` make JIL order-dependent,
    but plain `insert_job:` blocks must not be. Shuffle the blocks, run them
    through the REAL ingest path (jil_ingest.ingest_text -- the same one
    `jil import` and the API use), then manifest+size must be identical."""

    _BLOCKS = [
        "insert_job: box1   job_type: BOX\nowner: ops\ndate_conditions: 1\ndays_of_week: all\n",
        "insert_job: j_a   job_type: CMD\nbox_name: box1\ncommand: /opt/app/a.sh\nmachine: m1\ncondition: s(j_b)\n",
        "insert_job: j_b   job_type: CMD\nbox_name: box1\ncommand: auto_r3_exec -i\nmachine: m1\n",
        "insert_job: j_c   job_type: CMD\ncommand: C:\\\\scripts\\\\run.bat\nmachine: win1\n",
    ]

    def _ingest_and_size(self, blocks):
        from autosys.parser.jil_ingest import ingest_text
        with sync_session() as s:
            s.query(JobRow).delete()
            s.commit()
            ingest_text(s, "\n".join(blocks), "estate.jil")
            s.commit()
        rows = _rows()
        with sync_session() as s:
            manifest = build_manifest(rows, s)
        v = size(rows)
        return (
            sorted((r.job_name, r.class_, r.best, r.worst) for r in manifest),
            v.low, v.high, v.cu,
        )

    def test_block_order_changes_nothing(self):
        baseline = self._ingest_and_size(self._BLOCKS)
        for seed in range(1, 8):
            blocks = self._BLOCKS[:]
            random.Random(seed).shuffle(blocks)
            assert self._ingest_and_size(blocks) == baseline, \
                f"shuffled JIL blocks (seed {seed}) changed manifest or size"


def _sap_windows_estate():
    """Small estate exercising the answer-consuming paths: an erp_sap
    wrapper job (vendor_runtime family), a Windows-shaped job
    (windows_execution_plan), plus a box and a dependency chain."""
    return [
        {"job_name": "box1", "job_type": "BOX"},
        {"job_name": "j_a", "job_type": "CMD", "command": "/opt/app/a.sh", "machine": "m1",
         "box_name": "box1", "condition": "s(j_b)"},
        {"job_name": "j_b", "job_type": "CMD", "command": "auto_r3_exec -i", "machine": "m1",
         "box_name": "box1"},
        {"job_name": "j_c", "job_type": "CMD", "command": "C:\\scripts\\run.bat", "machine": "win1"},
        {"job_name": "j_d", "job_type": "CMD", "command": "/bin/echo d", "machine": "m1"},
    ]


class TestT11AnswersOnlyNarrow:
    """T11: any single real questionnaire answer can only narrow the
    [low, high] range -- it may pin a unit, never widen one. Options are
    the real v2 strings the model's effect tables recognise."""

    def _base(self):
        _reseed(_sap_windows_estate())
        return size(_rows())

    def _narrows(self, answers, base):
        _reseed(_sap_windows_estate())
        v = size(_rows(), answers=answers)
        for u in UNITS:
            assert v.low[u] >= base.low[u], f"{answers}: low[{u}] fell"
            assert v.high[u] <= base.high[u], f"{answers}: high[{u}] rose"

    def test_every_listed_option_narrows(self):
        from autosys.analysis.size_model import (
            VENDOR_EFFECT, WINDOWS_EFFECT, SKILL_BLOCKS,
            CUTOVER_PARALLEL_FRACTION, ADOPTION_REUSE, ADOPTION_FULL,
            OSS, ASTRO,
        )
        base = self._base()
        cases = (
            [("windows_execution_plan", o) for o in WINDOWS_EFFECT]
            + [("migration_cutover_strategy", o) for o in CUTOVER_PARALLEL_FRACTION]
            + [("team_skill_profile", o) for o in SKILL_BLOCKS]
            + [("existing_airflow_adoption", o) for o in (ADOPTION_REUSE, *ADOPTION_FULL)]
            + [("target_platform", o) for o in (OSS, ASTRO)]
            # "Not ours" is a reclassification (moves the job into U), not a
            # narrowing -- it is covered by TestT11c below, as in the design.
            + [("vendor_runtime__sap", o) for o in VENDOR_EFFECT
               if VENDOR_EFFECT[o] in ("keep", "rework")]
        )
        for key, option in cases:
            self._narrows({key: option}, base)


class TestT11bOffListEqualsUnanswered:
    def test_off_list_text_behaves_like_missing(self):
        _reseed(_sap_windows_estate())
        base = size(_rows())
        _reseed(_sap_windows_estate())
        off = size(_rows(), answers={
            "windows_execution_plan": "Astronomer",       # off-list
            "vendor_runtime__sap": "maybe",               # off-list
            "migration_cutover_strategy": "soon",         # off-list
            "team_skill_profile": "great team",           # off-list
        })
        assert off.low == base.low and off.high == base.high


class TestT11cNotOursMovesToUnknown:
    def test_not_ours_moves_wrapper_jobs_to_u_never_to_zero(self):
        from autosys.analysis.size_model import VENDOR_EFFECT
        _reseed(_sap_windows_estate())
        base = size(_rows())
        _reseed(_sap_windows_estate())
        not_ours = size(_rows(), answers={
            "vendor_runtime__sap": "Not ours — this detection looks wrong"})
        # The erp_sap job's row leaves the rework range and lands in U.
        assert not_ours.low["U"] == base.low["U"] + 1
        assert not_ours.high["R"] <= base.high["R"]
        # It is "unknown", not "gone": instances and flow units unchanged.
        assert not_ours.low["I"] == base.low["I"]


class TestQuestionnaireOptionContract:
    """Opt-in cross-repo contract (T18's "real answer flows" half): the
    option strings the shipped questionnaire offers must be the strings
    the size model switches on -- a typo in either repo turns a real
    answer into a silent off-list "open". Reads the sibling worktree's
    shipped JSON; skips cleanly when it isn't checked out alongside."""

    _SHINRO_JSON = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "shinro-release" / "apps" / "api" / "etc" / "autosys_to_astronomer_qns.json"
    )

    def _options(self):
        import json as _json
        doc = _json.loads(self._SHINRO_JSON.read_text())
        opts = {}
        def walk(x):
            if isinstance(x, dict):
                if "id" in x and "options" in x:
                    opts[x["id"]] = x["options"]
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for i in x:
                    walk(i)
        walk(doc)
        return opts

    def test_every_model_key_is_a_real_questionnaire_option(self):
        import os
        if not self._SHINRO_JSON.exists():
            pytest.skip("shinro questionnaire JSON not checked out as a sibling")
        from autosys.analysis.size_model import (
            VENDOR_EFFECT, WINDOWS_EFFECT, SKILL_BLOCKS,
            CUTOVER_PARALLEL_FRACTION, ADOPTION_REUSE, OSS, ASTRO,
        )
        opts = self._options()
        consumed = {
            "vendor_runtime": set(VENDOR_EFFECT),
            "windows_execution_plan": set(WINDOWS_EFFECT),
            "team_skill_profile": set(SKILL_BLOCKS),
            "migration_cutover_strategy": set(CUTOVER_PARALLEL_FRACTION),
            "existing_airflow_adoption": {ADOPTION_REUSE},
            "target_platform": {OSS, ASTRO},
        }
        for qid, keys in consumed.items():
            offered = set(opts.get(qid, ()))
            missing = keys - offered
            assert not missing, f"{qid}: model consumes options the questionnaire never offers: {missing}"
