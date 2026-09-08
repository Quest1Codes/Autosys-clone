"""Phase 9 — Connection Profiles tests."""
from __future__ import annotations
import json, pytest
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.repository import profiles as profile_repo

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'test_cp.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines(); create_all_sync(); yield; reset_engines()

class TestConnectionProfileRepository:

    def test_upsert_insert(self, fresh_db):
        with sync_session() as s:
            r = profile_repo.upsert(s, "hadoop1", "Hadoop", json.dumps({"namenode": "hdfs://nn1"}))
            s.commit()
        assert r == "inserted"
        with sync_session() as s:
            row = profile_repo.get(s, "hadoop1")
            assert row.profile_type == "Hadoop"
            assert json.loads(row.attributes_json)["namenode"] == "hdfs://nn1"

    def test_upsert_update(self, fresh_db):
        with sync_session() as s:
            profile_repo.upsert(s, "aws1", "AWS", json.dumps({"region": "us-east-1"}))
            s.commit()
        with sync_session() as s:
            r = profile_repo.upsert(s, "aws1", "AWS", json.dumps({"region": "us-west-2"}))
            s.commit()
        assert r == "updated"
        with sync_session() as s:
            row = profile_repo.get(s, "aws1")
            assert json.loads(row.attributes_json)["region"] == "us-west-2"

    def test_delete(self, fresh_db):
        with sync_session() as s:
            profile_repo.upsert(s, "del", "Hive", "{}"); s.commit()
        with sync_session() as s:
            assert profile_repo.delete(s, "del") is True; s.commit()
        with sync_session() as s:
            assert profile_repo.get(s, "del") is None

    def test_delete_not_found(self, fresh_db):
        with sync_session() as s:
            assert profile_repo.delete(s, "nope") is False

    def test_list_all(self, fresh_db):
        with sync_session() as s:
            profile_repo.upsert(s, "p_b", "S3", "{}")
            profile_repo.upsert(s, "p_a", "HDFS", "{}"); s.commit()
        with sync_session() as s:
            rows = profile_repo.list_all(s)
            assert len(rows) >= 2
            assert rows[0].profile_name == "p_a"

    def test_profile_types(self, fresh_db):
        types = ["Hadoop", "AWS", "Hive", "S3", "HDFS", "GCP"]
        with sync_session() as s:
            for i, t in enumerate(types):
                profile_repo.upsert(s, f"prof_{i}", t, json.dumps({"type": t}))
            s.commit()
        with sync_session() as s:
            rows = profile_repo.list_all(s)
            found_types = {r.profile_type for r in rows}
            for t in types:
                assert t in found_types

    def test_attributes_json_none(self, fresh_db):
        with sync_session() as s:
            profile_repo.upsert(s, "no_attrs", "Custom", None); s.commit()
        with sync_session() as s:
            row = profile_repo.get(s, "no_attrs")
            assert row.attributes_json is None
