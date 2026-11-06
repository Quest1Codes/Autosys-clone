"""
T04: the conformance suite's estate fixtures.

"The synthetic estates" means `tests/synthetic/gen_estate.py`'s 10
archetypes -- committed, because they're synthetic (every generated file
says so in its own header). "The 8 samples" in 07-build-tasks.md's wording
means the real-shaped JIL files under `jil_samples/`, which the project's
own gitignore rule keeps out of the repo entirely (added while fixing T01).
This file corrects that: the real samples are opt-in and skipped cleanly
when absent, so a fresh clone still has a full conformance suite with no
real or real-shaped data anywhere in it.
"""
from __future__ import annotations

import glob
import os

import pytest

from tests.synthetic.gen_estate import ARCHETYPES, generate


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_synthetic.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection as _conn
    _conn.reset_engines()
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=False)
    yield
    _conn.reset_engines()


@pytest.fixture
def ssa_client(isolated_db):
    from fastapi.testclient import TestClient
    from autosys.app_server.main import create_app
    with TestClient(create_app(start_eps=False), raise_server_exceptions=True) as c:
        yield c


class TestDeterminism:
    """P9 in 05-vishy-decisions.md: same input, same output."""

    @pytest.mark.parametrize("archetype", sorted(ARCHETYPES))
    def test_same_seed_gives_byte_identical_text(self, archetype):
        t1, f1 = generate(archetype, n_jobs=200, seed=9)
        t2, f2 = generate(archetype, n_jobs=200, seed=9)
        assert t1 == t2
        assert f1 == f2

    def test_seed_currently_has_no_effect_on_job_content(self):
        # Checked, not assumed: no feature function in gen_estate.py calls
        # a method on the random.Random it's handed (`grep -n "rnd\\." ...`
        # finds nothing), so job content is the same at any seed today.
        # Whole-file text still differs across seeds, because the header
        # comment prints the seed value itself ("archetype=... seed=...") --
        # that's the one line this strips before comparing. If a future
        # feature block starts using rnd for real variation, this test is
        # the one that should start failing, as the signal to update it
        # rather than an accidental regression.
        for archetype in ARCHETYPES:
            bodies = {generate(archetype, 200, s)[0].split("\n", 1)[1] for s in (1, 2, 3)}
            assert len(bodies) == 1, f"{archetype} now varies by seed -- update this test's assumption"

    def test_facts_job_count_matches_the_text(self):
        for archetype in ARCHETYPES:
            text, facts = generate(archetype, n_jobs=200, seed=9)
            assert text.count("insert_job: ") == facts["n_jobs_defined"]
            assert len(facts["job_names"]) == facts["n_jobs_defined"]
            assert len(set(facts["job_names"])) == len(facts["job_names"]), "duplicate job name"


class TestRealImport:
    """Every archetype has to actually mean something to the real parser,
    not just be well-formed-looking text."""

    @pytest.mark.parametrize("archetype", sorted(ARCHETYPES))
    def test_imports_with_no_error_and_right_count(self, archetype, ssa_client):
        text, facts = generate(archetype, n_jobs=120, seed=3)

        r = ssa_client.post("/api/v1/jil/import", json={"content": text})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"], body.get("error")
        # Every stanza this generator writes is documented JIL syntax, so
        # none of it should be silently dropped (I7/I8 in prompt 09's
        # research) or archived-but-not-loaded.
        assert body["n_quarantined"] == 0, "a stanza this generator wrote wasn't recognised as JIL at all"
        assert body["n_failed"] == 0, "a parsed stanza was refused by the database"

        report = ssa_client.get("/api/v1/assessment/report")
        assert report.status_code == 200
        # update_job/override_job (feat_objects) touch an existing row, so
        # they don't change the count; only inserts and deletes do.
        # Deletions (delete_job: OBJ_0005) mean the stored count can be one
        # less than facts["n_jobs_defined"] -- check against what the
        # import itself reported, not a derived expectation.
        assert report.json()["job_count"] == body["n_inserted"] - body["n_deleted"]


class TestSyntheticTextNeverLooksLikeCustomerData:
    """A quick self-check: every archetype's header says what it is, and
    the generator doesn't accidentally echo real-looking identifiers."""

    @pytest.mark.parametrize("archetype", sorted(ARCHETYPES))
    def test_header_states_its_own_nature(self, archetype):
        text, _ = generate(archetype, n_jobs=50, seed=1)
        assert "not customer data" in text
        assert "synthetic estate" in text


# ---------------------------------------------------------------------------
# Opt-in: the real-shaped jil_samples/, if a developer's own checkout has
# them. Gitignored, so CI and a fresh clone collect 0 tests here, cleanly.
# ---------------------------------------------------------------------------

_SAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "jil_samples")
_SAMPLE_FILES = sorted(glob.glob(os.path.join(_SAMPLES_DIR, "*.jil"))) if os.path.isdir(_SAMPLES_DIR) else []


@pytest.mark.skipif(not _SAMPLE_FILES, reason="jil_samples/ not present locally (gitignored; opt-in only)")
class TestRealSampleImport:
    """Runs only when a developer has their own local jil_samples/. Proves
    nothing about CI or a fresh clone, and must never be relied on as the
    only coverage for anything -- see TestRealImport above for the part of
    this suite every clone actually runs."""

    @pytest.mark.parametrize("path", _SAMPLE_FILES, ids=[os.path.basename(p) for p in _SAMPLE_FILES])
    def test_sample_imports_with_no_error(self, path, ssa_client):
        r = ssa_client.post("/api/v1/jil/import", json={"content": open(path).read()})
        assert r.status_code == 200, r.text
