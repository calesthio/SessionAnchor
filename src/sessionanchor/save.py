"""Write context entries — add, complete, archive, promote, session-end.

Claude calls this during or at the end of a session to persist context.
"""

import json
import os
import sys
import uuid

from .store import (
    archive_entry,
    complete_entry,
    detect_project_name,
    end_session,
    get_connection,
    get_db_path,
    init_db,
    make_id,
    start_session,
    upsert_entry,
    CATEGORIES,
)


def cmd_add(parsed, conn):
    project = parsed.project or detect_project_name()
    tags = json.loads(parsed.tags) if parsed.tags else []
    entry_id = upsert_entry(
        conn, project=project, tier=parsed.tier, category=parsed.category,
        title=parsed.title, content=parsed.content, tags=tags,
        expires_at=parsed.expires, session_id=parsed.session_id,
        supersedes=parsed.supersedes,
    )
    print(f"OK: {entry_id} [{parsed.tier}] {parsed.category}/{parsed.title}")


def cmd_complete(parsed, conn):
    project = parsed.project or detect_project_name()
    entry_id = make_id(project, parsed.category, parsed.title)
    complete_entry(conn, entry_id)
    print(f"Completed: {entry_id}")


def cmd_archive(parsed, conn):
    project = parsed.project or detect_project_name()
    entry_id = make_id(project, parsed.category, parsed.title)
    archive_entry(conn, entry_id)
    print(f"Archived: {entry_id}")


def cmd_promote(parsed, conn):
    project = parsed.project or detect_project_name()
    entry_id = make_id(project, parsed.category, parsed.title)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE entries SET tier = ?, updated_at = ? WHERE id = ?",
                 (parsed.new_tier, now, entry_id))
    conn.commit()
    print(f"Promoted to {parsed.new_tier}: {entry_id}")


def cmd_session_end(parsed, conn):
    project = parsed.project or detect_project_name()
    session_id = parsed.session_id or str(uuid.uuid4())[:8]
    start_session(conn, session_id, project=project)
    end_session(conn, session_id, summary=parsed.summary,
                l0_tokens=parsed.l0_tokens or 0,
                l1_tokens=parsed.l1_tokens or 0,
                total_tokens=(parsed.l0_tokens or 0) + (parsed.l1_tokens or 0))
    print(f"Session recorded: {session_id}")


def cmd_bulk(parsed, conn):
    project_default = parsed.project or detect_project_name()
    with open(parsed.file, "r", encoding="utf-8") as f:
        entries = json.load(f)
    count = 0
    for e in entries:
        upsert_entry(
            conn, project=e.get("project", project_default),
            tier=e.get("tier", "L2"), category=e["category"],
            title=e["title"], content=e["content"],
            tags=e.get("tags", []), status=e.get("status", "active"),
            expires_at=e.get("expires_at"),
            supersedes=e.get("supersedes"),
        )
        count += 1
    print(f"Imported {count} entries.")


def main(args=None):
    """CLI entry point for save command."""
    import argparse

    parser = argparse.ArgumentParser(description="Save to memory store")
    parser.add_argument("--db", default=None)
    sub = parser.add_subparsers(dest="command")

    # add
    p_add = sub.add_parser("add", help="Add or update an entry")
    p_add.add_argument("--tier", required=True, choices=["L0", "L1", "L2"])
    p_add.add_argument("--category", required=True)
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--content", required=True)
    p_add.add_argument("--project", default=None)
    p_add.add_argument("--tags", default=None, help="JSON array of tags")
    p_add.add_argument("--expires", default=None)
    p_add.add_argument("--session-id", default=None)
    p_add.add_argument("--supersedes", default=None, help="Entry ID replaced by this entry")

    # complete
    p_comp = sub.add_parser("complete", help="Mark entry as completed")
    p_comp.add_argument("--title", required=True)
    p_comp.add_argument("--category", required=True)
    p_comp.add_argument("--project", default=None)

    # archive
    p_arch = sub.add_parser("archive", help="Mark entry as archived")
    p_arch.add_argument("--title", required=True)
    p_arch.add_argument("--category", required=True)
    p_arch.add_argument("--project", default=None)

    # promote
    p_prom = sub.add_parser("promote", help="Change entry tier")
    p_prom.add_argument("--title", required=True)
    p_prom.add_argument("--category", required=True)
    p_prom.add_argument("--new-tier", required=True, choices=["L0", "L1", "L2"])
    p_prom.add_argument("--project", default=None)

    # session-end
    p_sess = sub.add_parser("session-end", help="Record session end")
    p_sess.add_argument("--session-id", default=None)
    p_sess.add_argument("--summary", default=None)
    p_sess.add_argument("--project", default=None)
    p_sess.add_argument("--l0-tokens", type=int, default=None)
    p_sess.add_argument("--l1-tokens", type=int, default=None)

    # bulk
    p_bulk = sub.add_parser("bulk", help="Bulk import from JSON file")
    p_bulk.add_argument("--file", required=True)
    p_bulk.add_argument("--project", default=None)

    parsed = parser.parse_args(args)
    if not parsed.command:
        parser.print_help()
        return

    db_path = parsed.db or get_db_path()
    conn = get_connection(db_path)
    init_db(conn)

    dispatch = {
        "add": cmd_add,
        "complete": cmd_complete,
        "archive": cmd_archive,
        "promote": cmd_promote,
        "session-end": cmd_session_end,
        "bulk": cmd_bulk,
    }
    dispatch[parsed.command](parsed, conn)
    conn.close()
