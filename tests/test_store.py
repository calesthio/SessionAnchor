"""Tests for the core SQLite memory store."""

import os
import sqlite3
import tempfile

import pytest

from claude_context.store import (
    archive_entry,
    complete_entry,
    expire_stale_entries,
    format_boot_context,
    format_entry_compact,
    get_connection,
    get_l0,
    get_l1,
    get_l2,
    get_stats,
    init_db,
    make_id,
    search_entries,
    start_session,
    end_session,
    get_last_session,
    upsert_entry,
)


@pytest.fixture
def db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = get_connection(path)
    init_db(conn)
    yield conn
    conn.close()
    os.unlink(path)


class TestMakeId:
    def test_deterministic(self):
        id1 = make_id("proj", "decision", "Use SQLite")
        id2 = make_id("proj", "decision", "Use SQLite")
        assert id1 == id2

    def test_case_insensitive(self):
        id1 = make_id("Proj", "Decision", "Use SQLite")
        id2 = make_id("proj", "decision", "use sqlite")
        assert id1 == id2

    def test_different_inputs(self):
        id1 = make_id("proj", "decision", "Use SQLite")
        id2 = make_id("proj", "decision", "Use Postgres")
        assert id1 != id2

    def test_length(self):
        entry_id = make_id("proj", "decision", "Title")
        assert len(entry_id) == 16


class TestConnection:
    def test_connection_uses_wal(self, db):
        row = db.execute("PRAGMA journal_mode").fetchone()
        assert row[0].lower() == "wal"


class TestUpsert:
    def test_insert(self, db):
        entry_id = upsert_entry(
            db, project="test", tier="L1", category="decision",
            title="Test decision", content="We decided to test",
        )
        assert len(entry_id) == 16
        row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        assert row is not None
        assert row["title"] == "Test decision"
        assert row["status"] == "active"

    def test_upsert_updates(self, db):
        id1 = upsert_entry(
            db, project="test", tier="L1", category="decision",
            title="Test", content="Version 1",
        )
        id2 = upsert_entry(
            db, project="test", tier="L1", category="decision",
            title="Test", content="Version 2",
        )
        assert id1 == id2
        row = db.execute("SELECT * FROM entries WHERE id = ?", (id1,)).fetchone()
        assert row["content"] == "Version 2"

    def test_tags_stored_as_json(self, db):
        entry_id = upsert_entry(
            db, project="test", tier="L1", category="decision",
            title="Tagged", content="Content", tags=["urgent", "auth"],
        )
        row = db.execute("SELECT tags FROM entries WHERE id = ?", (entry_id,)).fetchone()
        import json
        assert json.loads(row["tags"]) == ["urgent", "auth"]


class TestTieredRetrieval:
    def test_l0_retrieval(self, db):
        upsert_entry(db, project="test", tier="L0", category="identity",
                      title="Identity", content="Test project")
        upsert_entry(db, project="test", tier="L1", category="decision",
                      title="Decision", content="Decided")

        l0 = get_l0(db, project="test")
        assert len(l0) == 1
        assert l0[0]["title"] == "Identity"

    def test_l0_includes_global(self, db):
        upsert_entry(db, project="global", tier="L0", category="preference",
                      title="Style", content="Concise")
        upsert_entry(db, project="test", tier="L0", category="identity",
                      title="Identity", content="Test project")

        l0 = get_l0(db, project="test")
        assert len(l0) == 2

    def test_l1_retrieval(self, db):
        upsert_entry(db, project="test", tier="L1", category="decision",
                      title="Decision", content="Decided something")
        upsert_entry(db, project="test", tier="L2", category="technical_note",
                      title="Deep note", content="Deep detail")

        l1 = get_l1(db, project="test")
        assert len(l1) == 1
        assert l1[0]["title"] == "Decision"

    def test_l2_retrieval(self, db):
        upsert_entry(db, project="test", tier="L2", category="technical_note",
                      title="Deep note", content="Deep detail about auth")

        l2 = get_l2(db, project="test", category="technical_note")
        assert len(l2) == 1


class TestSearch:
    def test_fts_search(self, db):
        upsert_entry(db, project="test", tier="L1", category="decision",
                      title="Database choice", content="We chose SQLite for zero deps")
        upsert_entry(db, project="test", tier="L1", category="decision",
                      title="Auth choice", content="JWT tokens for stateless auth")

        results = search_entries(db, "SQLite", project="test")
        assert len(results) >= 1
        assert any("SQLite" in r["content"] for r in results)

    def test_search_tracks_access(self, db):
        entry_id = upsert_entry(
            db, project="test", tier="L1", category="decision",
            title="Database choice", content="We chose SQLite for zero deps",
        )
        search_entries(db, "SQLite", project="test")
        row = db.execute(
            "SELECT access_count, last_accessed_at FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        assert row["access_count"] == 1
        assert row["last_accessed_at"] is not None

    def test_search_no_results(self, db):
        results = search_entries(db, "nonexistent", project="test")
        assert len(results) == 0


class TestStatusChanges:
    def test_complete(self, db):
        entry_id = upsert_entry(
            db, project="test", tier="L1", category="action_item",
            title="Build feature", content="Feature desc",
        )
        complete_entry(db, entry_id)
        row = db.execute("SELECT status FROM entries WHERE id = ?", (entry_id,)).fetchone()
        assert row["status"] == "completed"

    def test_archive(self, db):
        entry_id = upsert_entry(
            db, project="test", tier="L1", category="session_log",
            title="Session 1", content="Did stuff",
        )
        archive_entry(db, entry_id)
        row = db.execute("SELECT status FROM entries WHERE id = ?", (entry_id,)).fetchone()
        assert row["status"] == "archived"

    def test_expire_stale(self, db):
        upsert_entry(
            db, project="test", tier="L1", category="deadline",
            title="Old deadline", content="Expired",
            expires_at="2020-01-01",
        )
        count = expire_stale_entries(db)
        assert count == 1


class TestSessions:
    def test_session_lifecycle(self, db):
        start_session(db, "sess-1", project="test")
        end_session(db, "sess-1", summary="Did things", l0_tokens=100, l1_tokens=200)

        last = get_last_session(db, project="test")
        assert last is not None
        assert last["summary"] == "Did things"
        assert last["l0_tokens"] == 100


class TestFormatting:
    def test_compact_entry(self):
        entry = {
            "category": "decision", "title": "Use SQLite",
            "content": "Zero deps", "status": "active", "expires_at": None,
        }
        result = format_entry_compact(entry)
        assert "[decision] Use SQLite" in result
        assert "Zero deps" in result

    def test_boot_context_has_token_footer(self, db):
        upsert_entry(db, project="test", tier="L0", category="identity",
                      title="Identity", content="Test project for testing")
        l0 = get_l0(db, project="test")
        result = format_boot_context(l0, [], project="test")
        assert "Boot tokens:" in result


class TestStats:
    def test_stats(self, db):
        upsert_entry(db, project="test", tier="L0", category="identity",
                      title="Identity", content="Test")
        upsert_entry(db, project="test", tier="L1", category="decision",
                      title="Decision", content="Decided")
        stats = get_stats(db, project="test")
        assert stats["by_tier"]["L0"] == 1
        assert stats["by_tier"]["L1"] == 1
