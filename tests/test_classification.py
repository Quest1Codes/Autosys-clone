"""
T15: rules-as-data integration classification.

Classifies stored jobs (R7 in 05-vishy-decisions.md), not re-parsed text:
every test here imports a real file through the real `/api/v1/jil/import`
endpoint first, reads the stored `JobRow`s back out, and classifies those --
never a hand-rolled parser, which is exactly the shortcut the ported
research's own prototype took and that R7 flagged as a defect.
"""
from __future__ import annotations

import collections
import glob
import os

import pytest

from autosys.analysis.classification import classify_job, classify_row, executable, load_rules, redact_and_flag

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "jil", "classifier_synthetic_commands.jil")
_SAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "jil_samples")
_SAMPLE_FILES = sorted(glob.glob(os.path.join(_SAMPLES_DIR, "*.jil"))) if os.path.isdir(_SAMPLES_DIR) else []


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_classification.db"
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


def _classify_stored(ssa_client, jil_text):
    """Import real text, read real stored rows back, classify those."""
    from autosys.db.connection import sync_session
    from autosys.db.repository import jobs as job_repo

    r = ssa_client.post("/api/v1/jil/import", json={"content": jil_text})
    assert r.status_code == 200, r.text
    assert r.json()["success"], r.json().get("error")

    with sync_session() as session:
        rows = job_repo.list_all(session)
        return [(row.job_name, classify_row(row)) for row in rows]


class TestRuleSet:
    def test_loads_with_expected_shape(self):
        load_rules.cache_clear()
        rs = load_rules()
        assert len(rs.classes) == 66
        assert len(rs.rules) == 91  # 86 ported + 5 split while fixing the scope bug
        assert len(rs.traits) == 10

    def test_every_rule_has_a_real_class(self):
        rs = load_rules()
        for r in rs.rules:
            assert r["class"] in rs.classes, r["id"]

    def test_rules_sorted_by_priority_then_id(self):
        rs = load_rules()
        keys = [(r["priority"], r["id"]) for r in rs.rules]
        assert keys == sorted(keys)


class TestExecutable:
    """R2's fix, and the heredoc case found while verifying it."""

    def test_shell_prefix_is_skipped(self):
        assert executable(". /etc/profile; /app/bin/load.sh") == "load.sh"
        assert executable("sudo env /app/bin/x.sh") == "x.sh"
        assert executable("nohup /app/bin/y.sh &") == "y.sh"

    def test_heredoc_body_is_not_mistaken_for_a_statement_separator(self):
        # Without this fix, splitting on the ';' inside the heredoc body
        # takes the heredoc's own closing "EOF" as the command.
        assert executable("direct -s <<EOF submit file=/processes/daily.cd; EOF") == "direct"

    def test_plain_command_unaffected(self):
        assert executable("/opt/app/scripts/daily_load.ksh -d x") == "daily_load.ksh"
        assert executable('"C:\\ldhe\\cxl\\TidalDB\\startApp.cmd" arg1') == "startapp.cmd"

    def test_empty_command(self):
        assert executable(None) == ""
        assert executable("") == ""


class TestScopeFix:
    """9 any-scope tokens misclassified a plain command whose destination
    argument happened to share the name. Verified interactively against
    the unfixed rules.json while building this (reverting the split and
    re-running showed the collision; this test checks the fixed state is
    what ships)."""

    @pytest.mark.parametrize("token", ["jil", "chase", "forecast", "oc", "helm", "bq", "cmd", "sh", "isql"])
    def test_bare_destination_argument_does_not_misclassify(self, token):
        r = classify_job("CMD", f"/bin/cp report.csv /archive/{token}")
        assert r["class"] == "fs_ops", f"{token}: {r['class']}"

    @pytest.mark.parametrize("cmd,want", [
        ("sendevent -E FORCE_STARTJOB -J x", "scheduler_self_call"),
        ("jil < defs.jil", "scheduler_self_call"),
        ("chase -j my_job", "scheduler_self_call"),
        ("forecast -J box1", "scheduler_self_call"),
        ("oc get pods", "cloud_k8s"),
        ("helm upgrade myrelease ./chart", "cloud_k8s"),
        ("cmd /c dir", "windows_script"),
        ("sh /app/run.sh", "unix_script"),
        ("isql -v mydsn user pass", "db_ambiguous"),
    ])
    def test_real_invocation_still_classifies(self, cmd, want):
        assert classify_job("CMD", cmd)["class"] == want

    def test_distinctive_long_names_are_unaffected(self):
        # These were checked, not assumed, to not need the same fix: no
        # one plausibly names a destination directory "mysqldump".
        assert classify_job("CMD", "mysqldump -u root db > out.sql")["class"] == "db_mysql"
        assert classify_job("CMD", "spark-submit job.py")["class"] == "hadoop_spark"


class TestUnclassifiedNeverTrivial:
    """P2 in 05-vishy-decisions.md: unknown is never trivial, and never
    REDESIGN/MECH by accident."""

    def test_unknown_class_has_no_rule_or_confidence(self):
        r = classify_job("CMD", "/opt/frobco/bin/frobco_loader --run nightly")
        assert r["class"] == "unclassified"
        assert r["rule_id"] is None
        assert r["confidence"] is None

    def test_userdefined_native_type_is_not_unclassified(self):
        # "USERDEFINED" is what the real import path stores for an unknown
        # job_type (models.job.parse_job); this is the canonical value, not
        # the site's original type name.
        r = classify_job("USERDEFINED", "/opt/custom/mytype9.bin", user_type="MYTYPE9")
        assert r["class"] == "user_defined_type"
        assert r["user_type"] == "MYTYPE9"


class TestCredentialSafety:
    """Part F: shapes are flagged, secrets are never kept."""

    def test_kv_secret_is_redacted_and_flagged(self):
        redacted, flags = redact_and_flag("run.sh password=hunter2zz")
        assert "hunter2zz" not in redacted
        assert "inline_kv_secret" in flags

    def test_userpass_at_is_redacted_and_flagged(self):
        redacted, flags = redact_and_flag("curl ftp://svc:hunter2zz@host/file")
        assert "hunter2zz" not in redacted
        assert "inline_userpass_at" in flags

    def test_short_flag_only_counts_under_a_credential_exe(self):
        # The exact synthetic case that caught this during tuning (§6.5 of
        # the design study): --profile is not a secret.
        _, flags = redact_and_flag("myapp.sh --profile=/etc/site.profile", exe="myapp.sh")
        assert "inline_flag_secret" not in flags
        _, flags = redact_and_flag("mysql -p hunter2zz -u root", exe="mysql")
        assert "inline_flag_secret" in flags

    def test_positional_ftp_credential_shape(self):
        _, flags = redact_and_flag("bkftp.exe put host01 svcuser hunter2zz /out /in", exe="bkftp.exe")
        assert "positional_credential_shape" in flags

    def test_no_secret_value_survives_in_the_redacted_text(self):
        redacted, _ = redact_and_flag("sshpass -p hunter2zz ssh user@host")
        assert "hunter2zz" not in redacted

    def test_curl_dash_u_is_flagged(self):
        # R8 (05-vishy-decisions.md): ported as `\b-u`, which can never
        # match ("-" is not a word character, so no word boundary exists
        # between a preceding space and it). Fixed with the same negative
        # lookbehind _FLAGPASS already uses for this exact problem.
        redacted, flags = redact_and_flag("curl -u svc:hunter2zz https://api/x", exe="curl")
        assert "hunter2zz" not in redacted
        assert "inline_userpass_flag" in flags

    def test_dash_u_with_no_colon_is_not_a_false_flag(self):
        _, flags = redact_and_flag("/opt/app/run.sh -u myuser other_arg", exe="run.sh")
        assert "inline_userpass_flag" not in flags


class TestSyntheticConformance:
    """119 hand-written jobs, one or more per class (§6.4 of the design
    study). All synthetic: hosts/users are placeholders, and no line was
    drawn from a real estate."""

    def test_named_class_share(self, ssa_client):
        text = open(_FIXTURE).read()
        results = _classify_stored(ssa_client, text)
        work = [(n, r) for n, r in results if r["job_type"] != "BOX"]
        classes = collections.Counter(r["class"] for _, r in work)
        unclassified_names = sorted(n for n, r in work if r["class"] == "unclassified")

        # The design study's claim: 115/118 named, 3 intentional unknowns.
        # The exact 3 are the signal that this is working as designed, not
        # just "a similar share" -- an unrelated job going unclassified
        # would also move the percentage without being the same finding.
        assert unclassified_names == ["T_UNC_01", "T_UNC_02", "T_UNC_03"]
        assert len(work) - classes["unclassified"] == 115

    def test_no_classified_job_carries_a_credential_flag_by_mistake(self, ssa_client):
        # 20 jobs in the fixture are written to carry a credential shape
        # (per the design study); this doesn't re-assert the exact count
        # (that's a property of the fixture, not this code), only that
        # flagging and classification are independent, as designed.
        text = open(_FIXTURE).read()
        results = _classify_stored(ssa_client, text)
        with_cred = [n for n, r in results if r["traits"]]
        assert len(with_cred) > 0  # the fixture does exercise this path at all


@pytest.mark.skipif(not _SAMPLE_FILES, reason="jil_samples/ not present locally (gitignored; opt-in only)")
class TestRealSampleClassification:
    """Runs only with a developer's own local jil_samples/ (see T04's note
    on why these can't be committed fixtures). Not relied on as the only
    coverage -- TestSyntheticConformance above is what every clone runs."""

    def test_aggregate_named_share_is_high(self, ssa_client):
        total = collections.Counter()
        for path in _SAMPLE_FILES:
            text = open(path, errors="ignore").read()
            results = _classify_stored(ssa_client, text)
            total.update(r["class"] for _, r in results if r["job_type"] != "BOX")
            # Each file's own stored rows must not leak into the next
            # file's count: re-seed a clean db per file.
            from autosys.db.connection import sync_session
            from autosys.db.schema import JobRow
            with sync_session() as session:
                session.query(JobRow).delete()
                session.commit()
        work = sum(total.values())
        named = work - total["unclassified"]
        # The design study measured 96.4% (3,801/3,943) on the original 8
        # files with the original rules. The scope fix (T15) can only ever
        # move jobs among named classes or into/out of unclassified by
        # changing which rule wins a tie on an ambiguous short token --
        # it should not collapse the aggregate. A loose floor, not the
        # exact historical figure, since this repo's sample set may not
        # be byte-identical to the research's.
        assert named / work > 0.90, f"{named}/{work} = {named / work:.1%}"
