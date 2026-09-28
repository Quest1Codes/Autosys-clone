"""
Applying parsed JIL to the database: update/override MERGE semantics, NULL
clearing, delete_box / delete_job on boxes, override delete, and sub-commands
that are parsed but not stored — through both the CLI and the REST API.
"""

from __future__ import annotations

import textwrap

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from autosys.cli.main import autosys
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.repository import jobs as job_repo


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines()
    create_all_sync(drop_first=False)
    yield
    reset_engines()


def cli_import(jil: str, tmp_path, expect_ok=True, strict=False):
    f = tmp_path / "x.jil"
    f.write_text(textwrap.dedent(jil))
    args = ["jil", "import", str(f)]
    if strict:
        args.append("--strict")
    r = CliRunner().invoke(autosys, args)
    if expect_ok:
        assert r.exit_code == 0, r.output
    return r


def row(name):
    with sync_session() as s:
        r = job_repo.get_row(s, name)
        if r is not None:
            s.expunge(r)
        return r


BASE = """
    insert_job: base   job_type: CMD
    command: echo hi
    machine: localhost
    owner: alice
    n_retrys: 3
    alarm_if_fail: 1
    max_run_alarm: 30
    description: original
"""


class TestUpdateMerges:
    """update_job / override_job change only what they name."""

    def test_update_keeps_untouched_attributes(self, tmp_path):
        cli_import(BASE, tmp_path)
        cli_import("update_job: base\ndescription: changed\n", tmp_path)
        r = row("base")
        assert r.description == "changed"
        assert r.n_retrys == 3 and r.alarm_if_fail is True and r.max_run_alarm == 30
        assert r.command == "echo hi" and r.owner == "alice"

    def test_update_with_inline_job_type_merges_too(self, tmp_path):
        cli_import(BASE, tmp_path)
        cli_import("update_job: base   job_type: CMD\ncommand: echo new\n", tmp_path)
        r = row("base")
        assert r.command == "echo new" and r.n_retrys == 3 and r.machine == "localhost"

    def test_override_merges(self, tmp_path):
        cli_import(BASE, tmp_path)
        cli_import("override_job: base\nowner: bob\n", tmp_path)
        r = row("base")
        assert r.owner == "bob" and r.n_retrys == 3

    def test_update_of_missing_job_is_skipped_not_inserted_as_junk(self, tmp_path):
        r = cli_import("update_job: ghost\ndescription: x\n", tmp_path)
        assert row("ghost") is None
        assert "SKIPPED" in r.output

    def test_update_schedule_attribute_normalised(self, tmp_path):
        cli_import(BASE, tmp_path)
        cli_import('update_job: base\nstart_times: "06:00","18:00"\ndays_of_week: mo,tu\n', tmp_path)
        r = row("base")
        assert r.start_times == "06:00,18:00" and r.days_of_week == "mo,tu"


class TestNullClears:

    def test_null_clears_nullable_column(self, tmp_path):
        cli_import(BASE, tmp_path)
        cli_import("update_job: base\ndescription: NULL\n", tmp_path)
        r = row("base")
        assert r.description is None
        assert r.n_retrys == 3          # untouched

    def test_null_resets_not_null_column_to_default(self, tmp_path):
        cli_import(BASE, tmp_path)
        cli_import("override_job: base\nalarm_if_fail: NULL\n", tmp_path)
        assert row("base").alarm_if_fail is False

    def test_null_lowercase(self, tmp_path):
        cli_import(BASE, tmp_path)
        cli_import("update_job: base\nowner: null\n", tmp_path)
        assert row("base").owner is None


class TestDeletes:

    def test_delete_box_cascades(self, tmp_path):
        cli_import("""
            insert_job: bx   job_type: BOX
            insert_job: c1   job_type: CMD
            command: x
            machine: m
            box_name: bx
        """, tmp_path)
        r = cli_import("delete_box: bx\n", tmp_path)
        assert row("bx") is None and row("c1") is None
        assert "box (2 jobs)" in r.output

    def test_delete_job_on_box_detaches_children(self, tmp_path):
        cli_import("""
            insert_job: bx   job_type: BOX
            insert_job: c1   job_type: CMD
            command: x
            machine: m
            box_name: bx
        """, tmp_path)
        cli_import("delete_job: bx\n", tmp_path)
        assert row("bx") is None
        assert row("c1") is not None and row("c1").box_name is None

    def test_override_delete_is_reported_not_crashing(self, tmp_path):
        cli_import(BASE, tmp_path)
        r = cli_import("override_job: base delete\n", tmp_path)
        assert "SKIPPED" in r.output and row("base") is not None


class TestParsedButNotStored:

    def test_views_filters_alert_policies_are_reported(self, tmp_path):
        r = cli_import("""
            insert_view: v1
            insert_filter: f1
            insert_alert_policy: FailedJobs
            job_status: TERMINATED
            delete_user: bob
        """, tmp_path)
        assert r.output.count("SKIPPED") == 4

    def test_update_blob_glob_connectionprofile_persist(self, tmp_path):
        cli_import("""
            insert_glob: g1
            update_glob: g1
            insert_connectionprofile: cp1
            update_connectionprofile: cp1
        """, tmp_path)


class TestLexErrorsSurfaceCleanly:

    def test_cli_reports_lex_error(self, tmp_path):
        r = cli_import("insert_bogus: x\n", tmp_path, expect_ok=False, strict=True)
        assert r.exit_code == 1 and "Unknown JIL sub-command" in r.output

    def test_hash_comment_file_imports(self, tmp_path):
        cli_import("# generated\n" + BASE, tmp_path)
        assert row("base") is not None


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(fresh_db):
    from autosys.app_server.main import create_app
    with TestClient(create_app(start_eps=False), raise_server_exceptions=True) as c:
        yield c


def api_import(client, jil, dry_run=False):
    r = client.post("/api/v1/jil/import", json={"content": textwrap.dedent(jil), "dry_run": dry_run})
    assert r.status_code == 200, r.text
    return r.json()


class TestApi:

    def test_non_job_stanza_no_longer_crashes(self, client):
        # an insert_resource stanza used to raise AttributeError (op.job is None).
        # The API now persists it via the same apply_operation() the CLI uses
        # (it used to hand-roll a narrower "not persisted via the API" list;
        # that was an artifact of this router's own old code, not a real
        # limit — the ingester behind it always could).
        body = api_import(client, "insert_resource: r1\namount: 5\nres_type: D\n")
        assert body["success"] is True
        assert body["jobs"][0]["action"] == "RESOURCE"
        from autosys.db.schema import VirtualResourceRow
        with sync_session() as s:
            assert s.get(VirtualResourceRow, "r1") is not None

    def test_validate_handles_non_job_stanza(self, client):
        r = client.post("/api/v1/jil/validate", json={"content": "insert_monbro: m1\n"})
        assert r.status_code == 200 and r.json()["success"] is True

    def test_update_merges_via_api(self, client):
        api_import(client, BASE)
        api_import(client, "update_job: base\ndescription: via api\n")
        r = row("base")
        assert r.description == "via api" and r.n_retrys == 3

    def test_delete_box_via_api(self, client):
        api_import(client, """
            insert_job: bx   job_type: BOX
            insert_job: c1   job_type: CMD
            command: x
            machine: m
            box_name: bx
        """)
        body = api_import(client, "delete_box: bx\n")
        assert body["n_deleted"] == 2 and row("c1") is None

    def test_lex_error_is_a_clean_failure(self, client):
        body = api_import(client, "insert_bogus: x\n")
        assert body["success"] is False and "Unknown JIL sub-command" in body["error"]

    def test_new_syntax_via_api(self, client):
        body = api_import(client, "# c\ninsert_job: a job_type: CMD command: echo hi machine: m owner: o\n")
        assert body["success"] is True
        r = row("a")
        assert r.command == "echo hi" and r.machine == "m" and r.owner == "o"


class TestInitialStatusAttribute:
    """PDF: status sets an initial status during insertion only."""

    @pytest.mark.parametrize("name,code", [
        ("on_ice", 7), ("ON_HOLD", 11), ("SUCCESS", 4), ("failure", 5),
        ("TERMINATED", 6), ("ON_NOEXEC", 16), ("INACTIVE", 8),
    ])
    def test_initial_status_persisted(self, tmp_path, name, code):
        cli_import(f"insert_job: s1   job_type: CMD\ncommand: dir\nmachine: localhost\nstatus: {name}\n", tmp_path)
        assert row("s1").status == code

    def test_no_status_means_inactive(self, tmp_path):
        cli_import(BASE, tmp_path)
        assert row("base").status == 8

    def test_status_not_allowed_on_update(self, tmp_path):
        cli_import(BASE, tmp_path)
        r = cli_import("update_job: base\nstatus: SUCCESS\n", tmp_path, expect_ok=False, strict=True)
        assert r.exit_code == 1 and "status attribute" in r.output

    def test_invalid_initial_status_rejected(self, tmp_path):
        r = cli_import("insert_job: s2   job_type: CMD\ncommand: x\nmachine: m\nstatus: RUNNING\n",
                       tmp_path, expect_ok=False, strict=True)
        assert r.exit_code == 1 and "invalid initial status" in r.output

    def test_reimport_does_not_reset_runtime_status(self, tmp_path):
        cli_import("insert_job: s3   job_type: CMD\ncommand: x\nmachine: m\nstatus: ON_ICE\n", tmp_path)
        cli_import("insert_job: s3   job_type: CMD\ncommand: y\nmachine: m\n", tmp_path)
        assert row("s3").status == 7 and row("s3").command == "y"


class TestPdfEnumAndRangeFixes:

    def test_job_load_zero_accepted(self, tmp_path):
        cli_import("insert_job: l0   job_type: CMD\ncommand: x\nmachine: m\njob_load: 0\n", tmp_path)
        assert row("l0").job_load == 0

    @pytest.mark.parametrize("attr,val", [("cpu_usage", "USED"), ("cpu_usage", "free"),
                                          ("disk_space", "FREE"), ("disk_space", "used")])
    def test_free_used_enums(self, tmp_path, attr, val):
        cli_import(f"insert_job: e1   job_type: CMD\ncommand: x\nmachine: m\n{attr}: {val}\n", tmp_path)
        assert getattr(row("e1"), attr) == val

    def test_numeric_cpu_usage_rejected(self, tmp_path):
        r = cli_import("insert_job: e2   job_type: CMD\ncommand: x\nmachine: m\ncpu_usage: 80\n",
                       tmp_path, expect_ok=False, strict=True)
        assert r.exit_code != 0


class TestExtraAttributesMerge:
    """Attributes with no dedicated column live in extra_attrs and merge on update."""

    def test_unknown_attributes_survive_insert_and_export(self, tmp_path):
        cli_import(BASE + "    sap_client: 100\n    sap_lang: EN\n", tmp_path)
        with sync_session() as s:
            job = job_repo.get(s, "base")
        assert job.extra_attrs == {"sap_client": "100", "sap_lang": "EN"}
        from autosys.parser.jil_writer import job_to_jil
        text = job_to_jil(job)
        assert "sap_client: 100" in text and "sap_lang: EN" in text

    def test_update_merges_extras(self, tmp_path):
        cli_import(BASE + "    sap_client: 100\n", tmp_path)
        cli_import("update_job: base\nsap_lang: DE\n", tmp_path)
        with sync_session() as s:
            assert job_repo.get(s, "base").extra_attrs == {"sap_client": "100", "sap_lang": "DE"}

    def test_null_removes_an_extra(self, tmp_path):
        cli_import(BASE + "    sap_client: 100\n    sap_lang: EN\n", tmp_path)
        cli_import("update_job: base\nsap_client: NULL\n", tmp_path)
        with sync_session() as s:
            assert job_repo.get(s, "base").extra_attrs == {"sap_lang": "EN"}
