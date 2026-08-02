"""
Phase 7 test suite — Box Orchestration, insert_machine JIL, MachineDef.

Coverage
--------
1.  MachineDef model         — validation, host defaulting
2.  JIL lexer                — insert_machine tokenised as DIRECTIVE
3.  JIL parser               — insert_machine parsed as JILOperation(machine=...)
4.  JIL writer               — machine_to_jil output
5.  jil import (CLI)         — insert_machine stanzas upsert machines table
6.  BoxManager               — activate children, complete box, kill children, reset
7.  JobRepository            — get_children, get_running_boxes, get_box_row
8.  EventProcessor           — box tick wired, FORCE_STARTJOB resets children,
                               KILLJOB kills children, box completes automatically
9.  CLI box status           — renders table
10. CLI box tree             — renders tree
11. Full pipeline            — import JIL with box + children, STARTJOB box,
                               tick until box SUCCESS
"""

from __future__ import annotations

import textwrap
import time
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from autosys.cli.main             import autosys
from autosys.db.connection        import sync_session, reset_engines
from autosys.db.migrations        import create_all_sync
from autosys.db.repository        import (
    jobs   as job_repo,
    events as event_repo,
    machines as machine_repo,
)
from autosys.models.event         import Event
from autosys.models.job           import BoxJob, CmdJob
from autosys.models.enums         import JobStatus
from autosys.models.machine       import MachineDef
from autosys.parser.jil_parser    import JILParser, JILParseError, parse_jil
from autosys.parser.jil_writer    import machine_to_jil
from autosys.parser.lexer         import Lexer, TokenKind
from autosys.scheduler.box_manager import BoxManager
from autosys.scheduler.event_processor import EventProcessor

_DEMO_JIL = Path(__file__).parents[1] / "examples" / "demo_etl.jil"


# ===========================================================================
# DB fixture
# ===========================================================================

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test7.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("AUTOSYS_DB_URL", url)
    reset_engines()
    create_all_sync(drop_first=False)
    yield url
    reset_engines()


# ===========================================================================
# Helpers
# ===========================================================================

def _seed_box(box_name="etl_box", **kw):
    job = BoxJob(job_name=box_name, job_type="BOX", **kw)
    with sync_session() as session:
        job_repo.upsert(session, job)


def _seed_cmd(name, box_name=None, condition=None, machine="localhost", **kw):
    job = CmdJob(
        job_name=name, job_type="CMD",
        command=f"echo {name}",
        machine=machine,
        box_name=box_name,
        condition=condition,
        **kw
    )
    with sync_session() as session:
        job_repo.upsert(session, job)


def _get_status(name) -> str:
    with sync_session() as session:
        row = job_repo.get_row(session, name)
        return row.status if row else None


def _set_status(name, status):
    if isinstance(status, str):
        from autosys.models.enums import JobStatus
        status = JobStatus[status].value
    with sync_session() as session:
        row = job_repo.get_row(session, name)
        if row:
            row.status = status


def _enqueue(event_type, job_name=None, **kw):
    ev = Event(event_type=event_type, job_name=job_name, source="internal", **kw)
    with sync_session() as session:
        event_repo.enqueue(session, ev)


def _tick(auto_complete=True, n=1):
    """Process n ticks with auto_complete (stub) dispatcher."""
    proc = EventProcessor(auto_complete=auto_complete)
    for _ in range(n):
        with sync_session() as session:
            proc.process_one_tick(session)


def _wait_status(name, expected, timeout=5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _get_status(name) == expected:
            return True
        time.sleep(0.05)
    return False


# ===========================================================================
# 1. MachineDef model
# ===========================================================================

class TestMachineDef:

    def test_basic_fields(self):
        m = MachineDef(machine_name="etl-01", host="10.0.0.1", port=7520)
        assert m.machine_name == "etl-01"
        assert m.host         == "10.0.0.1"
        assert m.port         == 7520

    def test_host_defaults_to_machine_name(self):
        m = MachineDef(machine_name="etl-01")
        assert m.host == "etl-01"

    def test_port_defaults_to_7520(self):
        m = MachineDef(machine_name="etl-01")
        assert m.port == 7520

    def test_type_defaults_to_a(self):
        m = MachineDef(machine_name="etl-01")
        assert m.type == "a"

    def test_max_load_defaults_to_100(self):
        m = MachineDef(machine_name="etl-01")
        assert m.max_load == 100

    def test_port_validation_range(self):
        with pytest.raises(Exception):
            MachineDef(machine_name="x", port=0)
        with pytest.raises(Exception):
            MachineDef(machine_name="x", port=99999)

    def test_description_optional(self):
        m = MachineDef(machine_name="etl-01", description="ETL worker")
        assert m.description == "ETL worker"

    def test_explicit_host_not_overridden(self):
        m = MachineDef(machine_name="etl-01", host="192.168.1.10")
        assert m.host == "192.168.1.10"


# ===========================================================================
# 2. JIL lexer — insert_machine
# ===========================================================================

class TestLexerInsertMachine:

    def test_insert_machine_tokenised_as_directive(self):
        tokens = Lexer().tokenize("insert_machine: etl-server-01")
        assert tokens[0].kind  == TokenKind.DIRECTIVE
        assert tokens[0].value == "insert_machine"

    def test_machine_name_is_job_name_token(self):
        tokens = Lexer().tokenize("insert_machine: etl-server-01")
        assert tokens[1].kind  == TokenKind.JOB_NAME
        assert tokens[1].value == "etl-server-01"

    def test_machine_with_port_attribute(self):
        text   = "insert_machine: srv1\n    port: 7520\n"
        tokens = Lexer().tokenize(text)
        types  = [t.kind for t in tokens if t.kind != TokenKind.EOF]
        assert TokenKind.DIRECTIVE in types
        assert TokenKind.ATTR_NAME in types
        assert TokenKind.VALUE     in types


# ===========================================================================
# 3. JIL parser — insert_machine stanza
# ===========================================================================

class TestParserInsertMachine:

    def _parse(self, text):
        return JILParser().parse_text(textwrap.dedent(text).strip())

    def test_insert_machine_produces_machine_op(self):
        ops = self._parse("""
            insert_machine: etl-server-01
                port: 7520
        """)
        assert len(ops) == 1
        assert ops[0].op      == "insert_machine"
        assert ops[0].machine is not None
        assert ops[0].job     is None

    def test_machine_name_parsed(self):
        ops = self._parse("insert_machine: etl-server-01")
        assert ops[0].machine.machine_name == "etl-server-01"

    def test_port_coerced_to_int(self):
        ops = self._parse("insert_machine: srv\n    port: 7521\n")
        assert ops[0].machine.port == 7521
        assert isinstance(ops[0].machine.port, int)

    def test_max_load_coerced_to_int(self):
        ops = self._parse("insert_machine: srv\n    max_load: 50\n")
        assert ops[0].machine.max_load == 50

    def test_host_parsed(self):
        ops = self._parse("insert_machine: srv\n    host: 192.168.1.1\n")
        assert ops[0].machine.host == "192.168.1.1"

    def test_host_defaults_when_omitted(self):
        ops = self._parse("insert_machine: etl-01")
        assert ops[0].machine.host == "etl-01"

    def test_mixed_job_and_machine_stanzas(self):
        text = textwrap.dedent("""
            insert_machine: srv1
                port: 7520

            insert_job: my_cmd   job_type: CMD
                command: echo hi
                machine: srv1
        """).strip()
        ops = JILParser().parse_text(text)
        assert len(ops) == 2
        assert ops[0].op == "insert_machine"
        assert ops[1].op == "insert"

    def test_raw_attrs_preserved(self):
        ops = self._parse("insert_machine: srv\n    port: 7520\n    max_load: 50\n")
        assert "port"     in ops[0].raw_attrs
        assert "max_load" in ops[0].raw_attrs


# ===========================================================================
# 4. JIL writer — machine_to_jil
# ===========================================================================

class TestMachineToJil:

    def test_contains_insert_machine(self):
        m = MachineDef(machine_name="etl-01")
        assert "insert_machine: etl-01" in machine_to_jil(m)

    def test_contains_type(self):
        m = MachineDef(machine_name="etl-01")
        assert "type: a" in machine_to_jil(m)

    def test_contains_port(self):
        m = MachineDef(machine_name="etl-01", port=7521)
        assert "port: 7521" in machine_to_jil(m)

    def test_host_included_when_different_from_name(self):
        m = MachineDef(machine_name="etl-01", host="192.168.1.1")
        assert "host: 192.168.1.1" in machine_to_jil(m)

    def test_host_omitted_when_same_as_name(self):
        m = MachineDef(machine_name="etl-01")
        # host defaults to machine_name — should be omitted (redundant)
        assert "host: etl-01" not in machine_to_jil(m)

    def test_roundtrip_parse(self):
        """machine_to_jil output can be re-parsed by JILParser."""
        m    = MachineDef(machine_name="etl-01", host="10.0.0.1", port=7522)
        text = machine_to_jil(m)
        ops  = JILParser().parse_text(text)
        assert ops[0].machine.machine_name == "etl-01"
        assert ops[0].machine.port         == 7522


# ===========================================================================
# 5. jil import CLI — insert_machine stanzas
# ===========================================================================

class TestJILImportMachine:

    def _run(self, *args):
        return CliRunner().invoke(autosys, list(args), catch_exceptions=False)

    def test_import_machine_exit_0(self, tmp_path):
        jil = tmp_path / "machines.jil"
        jil.write_text("insert_machine: etl-01\n    port: 7520\n")
        result = self._run("jil", "import", str(jil))
        assert result.exit_code == 0

    def test_import_machine_shows_machine_action(self, tmp_path):
        jil = tmp_path / "machines.jil"
        jil.write_text("insert_machine: etl-02\n    port: 7520\n")
        result = self._run("jil", "import", str(jil))
        assert "etl-02" in result.output

    def test_import_machine_creates_db_row(self, tmp_path):
        jil = tmp_path / "m.jil"
        jil.write_text("insert_machine: etl-03\n    host: 10.0.0.3\n    port: 7521\n")
        self._run("jil", "import", str(jil))
        with sync_session() as session:
            row = machine_repo.get(session, "etl-03")
        assert row is not None
        assert row.host == "10.0.0.3"
        assert row.port == 7521

    def test_import_mixed_jobs_and_machines(self, tmp_path):
        jil = tmp_path / "mix.jil"
        jil.write_text(textwrap.dedent("""
            insert_machine: etl-04
                port: 7520

            insert_job: my_cmd   job_type: CMD
                command: echo hi
                machine: etl-04
        """).strip())
        result = self._run("jil", "import", str(jil))
        assert result.exit_code == 0
        with sync_session() as session:
            assert machine_repo.get(session, "etl-04") is not None
            assert job_repo.get_row(session, "my_cmd")  is not None

    def test_import_machine_idempotent(self, tmp_path):
        jil = tmp_path / "m.jil"
        jil.write_text("insert_machine: etl-05\n    port: 7520\n")
        self._run("jil", "import", str(jil))
        self._run("jil", "import", str(jil))
        with sync_session() as session:
            rows = machine_repo.list_all(session)
        assert sum(1 for r in rows if r.machine_name == "etl-05") == 1


# ===========================================================================
# 6. BoxManager
# ===========================================================================

class TestBoxManager:

    def _make_box(self, box_name="etl_box", children=None):
        """Seed a box and its children, box in RUNNING state."""
        _seed_box(box_name)
        _set_status(box_name, "RUNNING")
        for child_name, condition in (children or []):
            _seed_cmd(child_name, box_name=box_name, condition=condition)

    def _bm_tick(self, auto_complete=True):
        bm = BoxManager(auto_complete=auto_complete)
        with sync_session() as session:
            snapshot = {r.job_name: r.status for r in job_repo.list_all(session)}
            return bm.tick(session, snapshot, datetime.now())

    def test_empty_box_completes_immediately(self):
        _seed_box("empty_box")
        _set_status("empty_box", "RUNNING")
        self._bm_tick()
        assert _get_status("empty_box") == 4

    def test_child_without_condition_activated(self):
        self._make_box(children=[("child_a", None)])
        self._bm_tick()
        assert _get_status("child_a") == 4  # auto_complete=True

    def test_child_with_met_condition_activated(self):
        self._make_box(children=[
            ("child_a", None),
            ("child_b", "s(child_a)"),
        ])
        _set_status("child_a", "SUCCESS")
        self._bm_tick()
        assert _get_status("child_b") == 4

    def test_child_with_unmet_condition_stays_inactive(self):
        """
        Snapshot is fixed at tick-start.  child_a activates in tick 1, but
        child_b's condition s(child_a) is evaluated against the START-of-tick
        snapshot (child_a still INACTIVE) → child_b stays INACTIVE until tick 2.
        """
        self._make_box(children=[
            ("child_a", None),
            ("child_b", "s(child_a)"),
        ])
        # After one tick: child_a activated (SUCCESS via auto_complete),
        # but child_b's condition was evaluated against the pre-tick snapshot
        # where child_a was still INACTIVE → child_b stays INACTIVE.
        self._bm_tick()
        assert _get_status("child_b") == 8

    def test_box_completes_success_when_all_children_done(self):
        self._make_box(children=[("child_a", None), ("child_b", None)])
        self._bm_tick()  # activates + auto-completes both children
        self._bm_tick()  # now all children terminal → box completes
        assert _get_status("etl_box") == 4

    def test_box_fails_if_child_fails(self):
        self._make_box(children=[("child_a", None)])
        _set_status("child_a", "FAILURE")
        self._bm_tick()
        assert _get_status("etl_box") == 5

    def test_box_terminated_if_child_terminated(self):
        self._make_box(children=[("child_a", None)])
        _set_status("child_a", "TERMINATED")
        self._bm_tick()
        assert _get_status("etl_box") == 6

    def test_box_not_completed_while_child_running(self):
        self._make_box(children=[("child_a", None)])
        _set_status("child_a", "RUNNING")
        self._bm_tick(auto_complete=False)
        # child still RUNNING → box stays RUNNING
        assert _get_status("etl_box") == 1

    def test_kill_children_terminates_active(self):
        self._make_box(children=[("child_a", None)])
        _set_status("child_a", "RUNNING")
        bm = BoxManager()
        with sync_session() as session:
            bm.kill_children(session, "etl_box", datetime.now())
        assert _get_status("child_a") == 6

    def test_kill_children_leaves_terminal_untouched(self):
        self._make_box(children=[("child_a", None), ("child_b", None)])
        _set_status("child_a", "SUCCESS")
        _set_status("child_b", "RUNNING")
        bm = BoxManager()
        with sync_session() as session:
            bm.kill_children(session, "etl_box", datetime.now())
        assert _get_status("child_a") == 4   # untouched
        assert _get_status("child_b") == 6

    def test_reset_children_sets_inactive(self):
        self._make_box(children=[("child_a", None)])
        _set_status("child_a", "SUCCESS")
        bm = BoxManager()
        with sync_session() as session:
            bm.reset_children(session, "etl_box")
        assert _get_status("child_a") == 8

    def test_sequential_chain_two_ticks(self):
        """chain: a → b (s(a)) — two ticks needed: tick1 activates a, tick2 activates b."""
        self._make_box(children=[("a", None), ("b", "s(a)")])
        self._bm_tick(auto_complete=True)  # a gets activated+completed; b's condition now met
        self._bm_tick(auto_complete=True)  # b gets activated+completed; box completes
        assert _get_status("a") == 4
        assert _get_status("b") == 4
        assert _get_status("etl_box") == 4


# ===========================================================================
# 7. JobRepository — box queries
# ===========================================================================

class TestBoxRepositoryMethods:

    def test_get_children_returns_children(self):
        _seed_box("box1")
        _seed_cmd("child1", box_name="box1")
        _seed_cmd("child2", box_name="box1")
        _seed_cmd("other")   # not in box1
        with sync_session() as session:
            children = job_repo.get_children(session, "box1")
        names = [c.job_name for c in children]
        assert "child1" in names
        assert "child2" in names
        assert "other"  not in names

    def test_get_children_empty_box(self):
        _seed_box("empty_box")
        with sync_session() as session:
            children = job_repo.get_children(session, "empty_box")
        assert children == []

    def test_get_running_boxes_returns_running(self):
        _seed_box("running_box")
        _seed_box("idle_box")
        _set_status("running_box", "RUNNING")
        with sync_session() as session:
            boxes = job_repo.get_running_boxes(session)
        names = [b.job_name for b in boxes]
        assert "running_box" in names
        assert "idle_box"    not in names

    def test_get_running_boxes_excludes_non_box(self):
        _seed_cmd("a_cmd")
        _set_status("a_cmd", "RUNNING")
        with sync_session() as session:
            boxes = job_repo.get_running_boxes(session)
        assert all(b.job_type == "BOX" for b in boxes)

    def test_get_box_row_returns_none_for_non_box(self):
        _seed_cmd("not_a_box")
        with sync_session() as session:
            result = job_repo.get_box_row(session, "not_a_box")
        assert result is None

    def test_get_box_row_returns_row_for_box(self):
        _seed_box("real_box")
        with sync_session() as session:
            result = job_repo.get_box_row(session, "real_box")
        assert result is not None
        assert result.job_type == "BOX"


# ===========================================================================
# 8. EventProcessor — box integration
# ===========================================================================

class TestEventProcessorBoxIntegration:

    def test_startjob_box_activates_unconditional_child(self):
        """STARTJOB on a BOX → children without conditions run immediately."""
        _seed_box("box1")
        _seed_cmd("child1", box_name="box1")   # no condition
        _enqueue("STARTJOB", "box1")
        _tick()   # tick 1: processes STARTJOB event → box RUNNING
        _tick()   # tick 2: box tick activates child → child SUCCESS, box SUCCESS
        assert _get_status("child1") == 4
        assert _get_status("box1")   == 4

    def test_startjob_box_chains_children(self):
        """
        Chain: child_a → child_b (s(child_a)).

        tick 1: STARTJOB event → box RUNNING
        tick 2: box tick activates child_a (no condition) → child_a SUCCESS
        tick 3: box tick sees s(child_a) met → child_b SUCCESS, box SUCCESS
        """
        _seed_box("chain_box")
        _seed_cmd("ca", box_name="chain_box")
        _seed_cmd("cb", box_name="chain_box", condition="s(ca)")
        _enqueue("STARTJOB", "chain_box")
        _tick(n=3)
        assert _get_status("ca")        == 4
        assert _get_status("cb")        == 4
        assert _get_status("chain_box") == 4

    def test_box_failure_propagates_from_failed_child(self):
        """
        Use auto_complete=False so child stays in STARTING after activation,
        then manually set it to FAILURE before the completion tick.
        """
        _seed_box("fail_box")
        _seed_cmd("bad_child", box_name="fail_box")
        _enqueue("STARTJOB", "fail_box")
        proc = EventProcessor(auto_complete=False)
        with sync_session() as session:
            proc.process_one_tick(session)   # box → RUNNING
        with sync_session() as session:
            proc.process_one_tick(session)   # box tick → child STARTING
        _set_status("bad_child", "FAILURE")  # simulate job failure
        with sync_session() as session:
            proc.process_one_tick(session)   # box tick sees FAILURE → box FAILURE
        assert _get_status("fail_box") == 5

    def test_killjob_box_terminates_children(self):
        """
        Use auto_complete=False so child stays STARTING when KILLJOB arrives.
        """
        _seed_box("kill_box")
        _seed_cmd("kc1", box_name="kill_box")
        _enqueue("STARTJOB", "kill_box")
        proc = EventProcessor(auto_complete=False)
        with sync_session() as session:
            proc.process_one_tick(session)   # box → RUNNING
        with sync_session() as session:
            proc.process_one_tick(session)   # box tick → kc1 STARTING
        # kc1 is now STARTING (non-terminal) — KILLJOB should kill it
        _enqueue("KILLJOB", "kill_box")
        with sync_session() as session:
            proc.process_one_tick(session)
        assert _get_status("kill_box") == 6
        assert _get_status("kc1")      == 6

    def test_force_startjob_resets_children(self):
        """
        FORCE_STARTJOB on a completed box should reset children and re-run them.
        Use auto_complete=False so we can observe the INACTIVE→STARTING transition
        between the reset (FORCE_STARTJOB tick) and the next tick.
        """
        _seed_box("fbox")
        _seed_cmd("fc1", box_name="fbox")
        # First run to completion with auto_complete=False so box reaches RUNNING
        proc_no_ac = EventProcessor(auto_complete=False)
        _enqueue("STARTJOB", "fbox")
        with sync_session() as session:
            proc_no_ac.process_one_tick(session)   # box ACTIVATED → RUNNING, fc1 STARTING
        # Manually complete fc1 to let box complete
        _set_status("fc1", "SUCCESS")
        with sync_session() as session:
            proc_no_ac.process_one_tick(session)   # box tick sees all SUCCESS → box SUCCESS
        assert _get_status("fbox") == 4

        # FORCE_STARTJOB with auto_complete=False — resets children then activates box
        _enqueue("FORCE_STARTJOB", "fbox")
        with sync_session() as session:
            proc_no_ac.process_one_tick(session)   # reset_children → fc1 INACTIVE → box ACTIVATED/RUNNING + fc1 STARTING
        # Child was reset and re-activated (STARTING), box is no longer SUCCESS
        assert _get_status("fbox") in (JobStatus.ACTIVATED.value, JobStatus.RUNNING.value)
        # fc1 was reset then re-activated; stub dispatcher leaves it RUNNING
        assert _get_status("fc1")  in (JobStatus.INACTIVE.value, JobStatus.STARTING.value, JobStatus.RUNNING.value)

    def test_box_not_completed_with_running_children(self):
        _seed_box("wait_box")
        _seed_cmd("slow_child", box_name="wait_box")
        _enqueue("STARTJOB", "wait_box")
        # Use auto_complete=False to leave child in RUNNING
        proc = EventProcessor(auto_complete=False)
        with sync_session() as session:
            proc.process_one_tick(session)   # box → RUNNING, child → STARTING
        with sync_session() as session:
            proc.process_one_tick(session)   # child still STARTING → box stays RUNNING
        assert _get_status("wait_box") in (JobStatus.RUNNING.value,)

    def test_empty_box_completes_on_first_tick(self):
        _seed_box("empty_box")
        _enqueue("STARTJOB", "empty_box")
        _tick()   # box → RUNNING
        _tick()   # box tick sees no children → box SUCCESS
        assert _get_status("empty_box") == 4


# ===========================================================================
# 9. CLI box status
# ===========================================================================

class TestCLIBoxStatus:

    def _run(self, *args):
        return CliRunner().invoke(autosys, list(args), catch_exceptions=False)

    def test_box_status_exit_0(self):
        _seed_box("status_box")
        _set_status("status_box", "RUNNING")
        result = self._run("box", "status", "status_box")
        assert result.exit_code == 0

    def test_box_status_shows_box_name(self):
        _seed_box("status_box2")
        result = self._run("box", "status", "status_box2")
        assert "status_box2" in result.output

    def test_box_status_shows_children(self):
        _seed_box("sb3")
        _seed_cmd("child_a", box_name="sb3")
        result = self._run("box", "status", "sb3")
        assert "child_a" in result.output

    def test_box_status_shows_condition(self):
        _seed_box("sb4")
        _seed_cmd("child_b", box_name="sb4", condition="s(child_a)")
        result = self._run("box", "status", "sb4")
        assert "s(child_a)" in result.output

    def test_box_status_unknown_job_exits_nonzero(self):
        result = self._run("box", "status", "no_such_box")
        assert result.exit_code != 0

    def test_box_status_on_cmd_job_exits_nonzero(self):
        _seed_cmd("just_a_cmd")
        result = self._run("box", "status", "just_a_cmd")
        assert result.exit_code != 0

    def test_box_status_empty_box_message(self):
        _seed_box("empty_box_cli")
        result = self._run("box", "status", "empty_box_cli")
        assert result.exit_code == 0
        assert "no children" in result.output


# ===========================================================================
# 10. CLI box tree
# ===========================================================================

class TestCLIBoxTree:

    def _run(self, *args):
        return CliRunner().invoke(autosys, list(args), catch_exceptions=False)

    def test_box_tree_exit_0(self):
        _seed_box("tree_box")
        result = self._run("box", "tree", "tree_box")
        assert result.exit_code == 0

    def test_box_tree_shows_box_name(self):
        _seed_box("tree_box2")
        result = self._run("box", "tree", "tree_box2")
        assert "tree_box2" in result.output

    def test_box_tree_shows_children(self):
        _seed_box("tree_box3")
        _seed_cmd("tc1", box_name="tree_box3")
        _seed_cmd("tc2", box_name="tree_box3", condition="s(tc1)")
        result = self._run("box", "tree", "tree_box3")
        assert "tc1" in result.output
        assert "tc2" in result.output

    def test_box_tree_unknown_job_exits_nonzero(self):
        result = self._run("box", "tree", "no_such_box")
        assert result.exit_code != 0


# ===========================================================================
# 11. Full pipeline integration test
# ===========================================================================

class TestFullBoxPipeline:

    def _run(self, *args):
        return CliRunner().invoke(autosys, list(args), catch_exceptions=False)

    def test_demo_etl_jil_has_box(self):
        """demo_etl.jil contains a BOX — verify it imports correctly."""
        result = self._run("jil", "import", str(_DEMO_JIL))
        assert result.exit_code == 0
        with sync_session() as session:
            box = job_repo.get_row(session, "demo_etl_box")
        assert box is not None
        assert box.job_type == "BOX"

    def test_startjob_box_from_jil_completes(self):
        """Full pipeline: import JIL, STARTJOB the box, tick until SUCCESS."""
        self._run("jil", "import", str(_DEMO_JIL))

        # Patch all children to localhost so auto_complete works without real dispatch
        with sync_session() as session:
            for row in job_repo.list_all(session):
                row.machine = "localhost"

        _enqueue("STARTJOB", "demo_etl_box")

        # Run up to 10 ticks (chain: box → check → extract → load → warehouse → email)
        _tick(n=10)

        # The box should complete in SUCCESS
        assert _get_status("demo_etl_box") == 4

    def test_insert_machine_in_jil_then_job_uses_it(self, tmp_path):
        """JIL with insert_machine + insert_job that references that machine."""
        jil = tmp_path / "full.jil"
        jil.write_text(textwrap.dedent("""
            insert_machine: etl-worker
                host: 10.0.0.10
                port: 7520

            insert_job: my_etl_job   job_type: CMD
                command: /scripts/etl.sh
                machine: etl-worker
        """).strip())
        result = self._run("jil", "import", str(jil))
        assert result.exit_code == 0
        with sync_session() as session:
            m = machine_repo.get(session, "etl-worker")
            j = job_repo.get_row(session, "my_etl_job")
        assert m is not None
        assert j.machine == "etl-worker"
