"""L1 compaction — prevents memory bloat over time.

Strategies:
- Time-based: L1 entries older than 14 days → demote to L2
- Count cap: Keep max 20 active L1 entries; excess → L2
- Completed: L1 entries completed > 3 days ago → L2
- Superseded: Archive entries that have been superseded

Runs automatically at boot, can also be triggered manually.
"""

import sqlite3
from datetime import datetime, timedelta, timezone


def compact_l1(
    conn: sqlite3.Connection,
    project: str | None = None,
    max_age_days: int = 14,
    max_entries: int = 20,
    completed_grace_days: int = 3,
) -> dict:
    """Run all compaction strategies. Returns counts of demoted entries."""
    now = datetime.now(timezone.utc).isoformat()
    results = {"time_demoted": 0, "count_demoted": 0, "completed_demoted": 0, "superseded_archived": 0}

    project_clause = ""
    project_params: list = []
    if project:
        project_clause = " AND (project = ? OR project = 'global')"
        project_params = [project]

    # 1. Time-based: L1 entries older than max_age_days → L2
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    cursor = conn.execute(
        f"UPDATE entries SET tier = 'L2', updated_at = ? "
        f"WHERE tier = 'L1' AND updated_at < ? AND status = 'active'{project_clause}",
        [now, cutoff] + project_params,
    )
    results["time_demoted"] = cursor.rowcount

    # 2. Completed items older than grace period → L2
    comp_cutoff = (datetime.now(timezone.utc) - timedelta(days=completed_grace_days)).isoformat()
    cursor = conn.execute(
        f"UPDATE entries SET tier = 'L2', updated_at = ? "
        f"WHERE tier = 'L1' AND status = 'completed' AND updated_at < ?{project_clause}",
        [now, comp_cutoff] + project_params,
    )
    results["completed_demoted"] = cursor.rowcount

    # 3. Superseded entries → archive
    superseded_rows = conn.execute(
        f"SELECT supersedes FROM entries "
        f"WHERE supersedes IS NOT NULL AND TRIM(supersedes) != '' AND status = 'active'{project_clause}",
        project_params,
    ).fetchall()
    superseded_ids: list[str] = []
    for row in superseded_rows:
        raw_value = row["supersedes"] if not isinstance(row, tuple) else row[0]
        superseded_ids.extend(
            entry_id.strip() for entry_id in raw_value.split(",") if entry_id.strip()
        )
    if superseded_ids:
        placeholders = ",".join("?" * len(superseded_ids))
        cursor = conn.execute(
            f"UPDATE entries SET status = 'archived', updated_at = ? "
            f"WHERE id IN ({placeholders}) AND status != 'archived'",
            [now] + superseded_ids,
        )
        results["superseded_archived"] = cursor.rowcount

    # 4. Count cap: if more than max_entries active L1, demote oldest
    rows = conn.execute(
        f"SELECT id FROM entries WHERE tier = 'L1' AND status = 'active'{project_clause} "
        f"ORDER BY updated_at DESC",
        project_params,
    ).fetchall()

    if len(rows) > max_entries:
        excess_ids = [r["id"] for r in rows[max_entries:]]
        placeholders = ",".join("?" * len(excess_ids))
        cursor = conn.execute(
            f"UPDATE entries SET tier = 'L2', updated_at = ? WHERE id IN ({placeholders})",
            [now] + excess_ids,
        )
        results["count_demoted"] = cursor.rowcount

    conn.commit()
    return results


def main(args=None):
    """CLI entry point for compact command."""
    import argparse
    import os

    from .store import get_connection, get_db_path, init_db, detect_project_name

    parser = argparse.ArgumentParser(description="Compact L1 memory")
    parser.add_argument("--project", "-p", default=None)
    parser.add_argument("--max-age", type=int, default=14, help="Max age in days for L1 entries")
    parser.add_argument("--max-entries", type=int, default=20, help="Max active L1 entries")
    parser.add_argument("--db", default=None)
    parsed = parser.parse_args(args)

    db_path = parsed.db or get_db_path()
    if not os.path.exists(db_path):
        print("No memory database found.")
        return

    conn = get_connection(db_path)
    init_db(conn)

    project = parsed.project or detect_project_name()
    results = compact_l1(conn, project=project,
                         max_age_days=parsed.max_age, max_entries=parsed.max_entries)
    conn.close()

    total = sum(results.values())
    if total == 0:
        print("Nothing to compact.")
    else:
        print(f"Compacted {total} entries:")
        for key, count in results.items():
            if count:
                print(f"  {key}: {count}")
