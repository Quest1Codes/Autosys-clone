"""
Calendars, global variables, machines and resources load as AutoSys exports
them (audit SEM-09, PARSER-05, PARSER-06, PARSER-07).

SEM-09     an autocal_asc calendar (MM/DD/YYYY HH:MM) crashed every
           scheduler tick, import-dir quarantined autocal_asc files, and
           `autocal import` crashed on them.
PARSER-07  `autorep -G ALL` / `sendevent -E SET_GLOBAL` had no loader, so every
           v(...) condition pointed at nothing.
PARSER-05  resources ignored amount / res_type / machine (capacity always 1),
           and update_resource reset the capacity.
PARSER-06  machines dropped node_name, type, opsys, max_load, factor, and
           update_machine reset host and port.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import select

CORPUS = Path(__file__).parent / "fixtures" / "jil" / "audit_corpus"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'e.db'}")
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    connection.reset_engines()
    create_all_sync()
    yield connection
    connection.reset_engines()


def _ingest(db, *paths):
    from autosys.parser.jil_ingest import ingest_paths
    return ingest_paths(db.sync_session, [Path(p) for p in paths])


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def _calendar(db, name):
    from autosys.db.schema import CalendarRow
    with db.sync_session() as s:
        row = s.get(CalendarRow, name)
        return (json.loads(row.dates_json), row.description) if row else None


def _stanzas(db):
    from autosys.db.schema import JilStanzaRow
    with db.sync_session() as s:
        return [(r.directive, r.object_name, r.disposition, r.raw_text)
                for r in s.scalars(select(JilStanzaRow).order_by(JilStanzaRow.seq))]


# --- SEM-09 calendars ------------------------------------------------------------

def test_autocal_export_loads_standard_and_flags_extended(db):
    summary = _ingest(db, CORPUS / "09_autocal_asc.jil")
    assert summary.dispositions.get("QUARANTINED", 0) == 0
    assert _calendar(db, "US_HOLIDAYS")[0] == ["2025-01-01", "2025-07-04", "2025-12-25"]
    dates, desc = _calendar(db, "BUS_DAYS")
    assert dates == [] and "not simulated" in desc and "condition=WORKD" in desc
    st = _stanzas(db)
    assert [d for d, *_ in st] == ["calendar", "extended_calendar"]
    assert st[1][2] == "LOADED_WITH_WARNINGS"
    assert "".join(r[3] for r in st) == (CORPUS / "09_autocal_asc.jil").read_text()   # lossless


def test_insert_calendar_accepts_autocal_dates(db, tmp_path):
    _ingest(db, _write(tmp_path, "c.jil", "insert_calendar: C1\ndates: 01/02/2025,2025-03-04\n"))
    assert _calendar(db, "C1")[0] == ["2025-01-02", "2025-03-04"]


def test_one_bad_calendar_does_not_stop_the_scheduler_tick(db):
    from autosys.db.schema import CalendarRow
    from autosys.scheduler.event_processor import EventProcessor
    with db.sync_session() as s:
        s.add(CalendarRow(calendar_name="BROKEN", dates_json=json.dumps(["not-a-date"])))
        s.add(CalendarRow(calendar_name="GOOD", dates_json=json.dumps(["01/01/2025 00:00"])))
        s.commit()
    ep = EventProcessor()
    with db.sync_session() as s:
        ep.process_one_tick(s, now=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc))   # must not raise
    assert ep._bad_calendars == {"BROKEN"}


def test_calendar_model_reads_autocal_dates():
    from autosys.models.calendar import Calendar
    cal = Calendar(calendar_name="X", dates="calendar: X\n01/01/2025 00:00\n07/04/2025 00:00\n")
    assert cal.dates == [date(2025, 1, 1), date(2025, 7, 4)]


def test_autocal_cli_import_reads_the_export(db):
    from autosys.cli.autocal_cmd import autocal_group
    res = CliRunner().invoke(autocal_group, ["import", str(CORPUS / "09_autocal_asc.jil")])
    assert res.exit_code == 0, res.output
    assert _calendar(db, "US_HOLIDAYS")[0][0] == "2025-01-01"


# --- PARSER-07 globals -------------------------------------------------------------

def _globals(db):
    from autosys.db.repository import globs
    with db.sync_session() as s:
        return globs.as_dict(s)


def test_sendevent_set_global_script_loads(db):
    _ingest(db, CORPUS / "10_globals.jil")
    assert _globals(db) == {"GL_READY": "Y", "PROC_DATE": "20250101"}


def test_autorep_g_table_loads_and_delete_removes(db, tmp_path):
    table = ("Global Name                      Value          Last Changed\n"
             "________________________________ ______________ ____________________\n"
             "GL_READY                         Y              01/02/2025 10:00:00\n"
             "BATCH_DATE                       20250102       01/02/2025 10:00:00\n")
    _ingest(db, _write(tmp_path, "g.txt", table))
    assert _globals(db) == {"GL_READY": "Y", "BATCH_DATE": "20250102"}
    _ingest(db, _write(tmp_path, "d.txt", 'sendevent -E SET_GLOBAL -G "GL_READY=DELETE"\n'))
    assert _globals(db) == {"BATCH_DATE": "20250102"}


def test_loaded_global_satisfies_a_value_condition(db):
    from autosys.parser.condition_parser import evaluate, parse_condition
    _ingest(db, CORPUS / "10_globals.jil")
    assert evaluate(parse_condition('v(GL_READY) = "Y"'), {}, _globals(db))


# --- PARSER-05 resources -------------------------------------------------------------

def _resource(db, name):
    from autosys.db.schema import VirtualResourceRow
    with db.sync_session() as s:
        r = s.get(VirtualResourceRow, name)
        return (r.max_load, r.res_type, r.machine, r.description)


def test_resource_amount_type_and_machine_are_stored(db, tmp_path):
    _ingest(db, _write(tmp_path, "r.jil",
                       "insert_resource: R1\nres_type: R\namount: 10\nmachine: m1\n"))
    assert _resource(db, "R1") == (10, "R", "m1", None)
    _ingest(db, _write(tmp_path, "u.jil", "update_resource: R1\ndescription: pool\n"))
    assert _resource(db, "R1") == (10, "R", "m1", "pool")          # capacity kept


# --- PARSER-06 machines ----------------------------------------------------------------

def _machine(db, name):
    from autosys.db.schema import MachineRow
    with db.sync_session() as s:
        m = s.get(MachineRow, name)
        return dict(host=m.host, port=m.port, type=m.machine_type, opsys=m.opsys,
                    max_load=m.max_load, factor=m.factor, description=m.description)


def test_machine_definition_is_stored_and_update_merges(db, tmp_path):
    _ingest(db, _write(tmp_path, "m.jil",
                       "insert_machine: winhost01\ntype: n\nnode_name: 10.1.2.3\nport: 7600\n"
                       "opsys: windows\nmax_load: 50\nfactor: 0.5\ndescription: \"win box\"\n"))
    want = dict(host="10.1.2.3", port=7600, type="n", opsys="windows", max_load=50,
                factor=0.5, description="win box")
    assert _machine(db, "winhost01") == want
    _ingest(db, _write(tmp_path, "u.jil", "update_machine: winhost01\ndescription: \"renamed\"\n"))
    assert _machine(db, "winhost01") == {**want, "description": "renamed"}
