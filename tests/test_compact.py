"""Tests for L1 compaction."""

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from sessionanchor.store import get_connection, init_db, upsert_entry
from sessionanchor.compact import compact_l1


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = get_connection(path)
    init_db(conn)
    yield conn
    conn.close()
    os.unlink(path)


def test_time_based_demotion(db):
    """Entries older than max_age_days should be demoted to L2."""
    # Insert an entry and backdate it
    entry_id = upsert_entry(
        db, project="test", tier="L1", category="decision",
        title="Old decision", content="Ancient",
    )
    old_date = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    db.execute("UPDATE entries SET updated_at = ? WHERE id = ?", (old_date, entry_id))
    db.commit()

    results = compact_l1(db, project="test", max_age_days=14)
    assert results["time_demoted"] == 1

    row = db.execute("SELECT tier FROM entries WHERE id = ?", (entry_id,)).fetchone()
    assert row["tier"] == "L2"


def test_completed_demotion(db):
    """Completed L1 entries older than grace period should be demoted."""
    entry_id = upsert_entry(
        db, project="test", tier="L1", category="action_item",
        title="Done task", content="Completed",
    )
    # Mark completed and backdate
    old_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    db.execute(
        "UPDATE entries SET status = 'completed', updated_at = ? WHERE id = ?",
        (old_date, entry_id),
    )
    db.commit()

    results = compact_l1(db, project="test", completed_grace_days=3)
    assert results["completed_demoted"] == 1


def test_count_cap(db):
    """Excess L1 entries beyond max should be demoted."""
    for i in range(25):
        upsert_entry(
            db, project="test", tier="L1", category="decision",
            title=f"Decision {i}", content=f"Content {i}",
        )

    results = compact_l1(db, project="test", max_entries=20)
    assert results["count_demoted"] == 5

    remaining = db.execute(
        "SELECT COUNT(*) as cnt FROM entries WHERE tier = 'L1' AND status = 'active'"
    ).fetchone()
    assert remaining["cnt"] == 20


def test_nothing_to_compact(db):
    """No entries should be compacted when everything is fresh."""
    upsert_entry(
        db, project="test", tier="L1", category="decision",
        title="Fresh decision", content="Just made",
    )
    results = compact_l1(db, project="test")
    assert sum(results.values()) == 0
