"""Boot script — outputs a compact context briefing at session start.

Replaces loading large markdown files with a ~2000-token structured briefing.
Runs automatic compaction before loading to prevent L1 bloat.
"""

import os
import sys

from .compact import compact_l1
from .store import (
    detect_project_name,
    expire_stale_entries,
    format_boot_context,
    get_connection,
    get_db_path,
    get_l0,
    get_l1,
    get_last_session,
    get_stats,
    init_db,
)


def boot(project: str | None = None, full: bool = False,
         db_path: str | None = None) -> str:
    """Generate the boot briefing. Returns the formatted string."""
    db = db_path or get_db_path()
    if not os.path.exists(db):
        return (
            "# No memory database found.\n"
            "# Run `sessionanchor init` to set up context memory for this project."
        )

    conn = get_connection(db)
    init_db(conn)

    # Housekeeping
    expire_stale_entries(conn)
    compact_l1(conn, project=project)

    # Auto-detect project if not specified
    if not project:
        project = detect_project_name()

    # Retrieve tiered context
    l0 = get_l0(conn, project=project)
    l1 = get_l1(conn, project=project)

    briefing = format_boot_context(l0, l1, project=project)

    if full:
        stats = get_stats(conn, project=project)
        lines = [
            briefing,
            "## Memory Store Stats",
            f"  Entries by tier: {stats.get('by_tier', {})}",
            f"  Entries by status: {stats.get('by_status', {})}",
            f"  DB size: {stats.get('db_size_kb', '?')} KB",
            f"  Total sessions: {stats.get('total_sessions', 0)}",
        ]
        last = get_last_session(conn, project=project)
        if last:
            lines.append(f"\n## Last Session")
            lines.append(f"  Date: {last.get('ended_at', '?')}")
            lines.append(f"  Summary: {last.get('summary', 'No summary')}")
            lines.append(
                f"  Tokens loaded: L0={last.get('l0_tokens', 0)} "
                f"L1={last.get('l1_tokens', 0)} total={last.get('total_tokens', 0)}"
            )
        briefing = "\n".join(lines)

    conn.close()
    return briefing


def main(args=None):
    """CLI entry point for boot command."""
    import argparse

    parser = argparse.ArgumentParser(description="Boot context briefing")
    parser.add_argument("--project", "-p", default=None)
    parser.add_argument("--full", action="store_true", help="Include stats and last session")
    parser.add_argument("--db", default=None)
    parsed = parser.parse_args(args)

    output = boot(project=parsed.project, full=parsed.full, db_path=parsed.db)
    sys.stdout.buffer.write(output.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()
