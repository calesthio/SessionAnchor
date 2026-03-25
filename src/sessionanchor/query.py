"""On-demand deep context retrieval (L2).

Called during a session when Claude needs specific historical context
that wasn't included in the boot briefing.
"""

import json
import os
import sys

from .store import (
    CATEGORIES,
    detect_project_name,
    format_entry_compact,
    get_connection,
    get_db_path,
    get_l2,
    get_stats,
    init_db,
    search_entries,
)


def main(args=None):
    """CLI entry point for query command."""
    import argparse
    from datetime import datetime, timedelta, timezone

    parser = argparse.ArgumentParser(description="Query the memory store")
    parser.add_argument("search", nargs="?", default=None, help="Full-text search query")
    parser.add_argument("--project", "-p", default=None)
    parser.add_argument("--category", "-c", default=None)
    parser.add_argument("--status", "-s", default=None)
    parser.add_argument("--since", type=int, default=None, help="Entries updated in last N days")
    parser.add_argument("--limit", "-n", type=int, default=20)
    parser.add_argument("--json", dest="as_json", action="store_true")
    parser.add_argument("--list-categories", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--db", default=None)
    parsed = parser.parse_args(args)

    if parsed.list_categories:
        for name, desc in sorted(CATEGORIES.items()):
            print(f"  {name:20s} {desc}")
        return

    db_path = parsed.db or get_db_path()
    if not os.path.exists(db_path):
        print("No memory database found. Run `sessionanchor init` first.")
        return

    conn = get_connection(db_path)
    init_db(conn)

    project = parsed.project or detect_project_name()

    if parsed.stats:
        stats = get_stats(conn, project=project)
        print(json.dumps(stats, indent=2))
        conn.close()
        return

    if parsed.search:
        results = search_entries(conn, parsed.search, project=project,
                                 category=parsed.category, limit=parsed.limit)
    else:
        results = get_l2(conn, project=project, category=parsed.category, limit=parsed.limit)

    if parsed.status:
        results = [r for r in results if r["status"] == parsed.status]

    if parsed.since:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=parsed.since)).isoformat()
        results = [r for r in results if r["updated_at"] > cutoff]

    if not results:
        print("No matching entries found.")
        conn.close()
        return

    if parsed.as_json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(f"# {len(results)} entries found\n")
        for entry in results:
            print(format_entry_compact(entry))
            print(f"    [project={entry['project']} tier={entry['tier']} "
                  f"updated={entry['updated_at'][:10]}]")
            print()

    conn.close()
