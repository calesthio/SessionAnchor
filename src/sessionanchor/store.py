"""Core SQLite memory store with tiered retrieval and full-text search.

Zero external dependencies — uses Python's built-in sqlite3 with FTS5.
WAL mode for safe concurrent reads (e.g. subagent writes while main reads).
Deterministic IDs enable natural dedup via upsert.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ignore import redact_secrets
from .tokens import estimate_tokens

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_NAME = "context.db"

TIER_L0 = "L0"  # Always loaded (~500 tokens) — identity, phase, top priorities
TIER_L1 = "L1"  # Session-relevant (~1500 tokens) — recent decisions, active items
TIER_L2 = "L2"  # On-demand (unlimited) — full history, deep technical notes

STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_ARCHIVED = "archived"
STATUS_EXPIRED = "expired"

CATEGORIES = {
    "identity": "Project identity, org structure",
    "preference": "Working conventions, style",
    "priority": "Current goals, ranked by urgency",
    "decision": "Decisions made, with rationale",
    "deadline": "Time-sensitive items",
    "action_item": "Tasks with owner and status",
    "blocker": "Things preventing progress",
    "session_log": "Summary of a completed session",
    "architecture": "System design, data flow",
    "technical_note": "Implementation details, fixes, gotchas",
    "infrastructure": "Deploy config, env vars, DNS",
    "pattern": "Reusable problem-solution pairs",
    "lesson": "Things learned from mistakes or successes",
}


# ---------------------------------------------------------------------------
# Project detection
# ---------------------------------------------------------------------------

def detect_project_name(repo_root: str | None = None) -> str:
    """Detect project name from git remote origin or directory name."""
    root = repo_root or find_repo_root()
    if not root:
        return "global"

    # Try git remote origin
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=root, timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Extract repo name from URL
            name = url.rstrip("/").rsplit("/", 1)[-1]
            if name.endswith(".git"):
                name = name[:-4]
            if name:
                return name
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fall back to directory name
    return os.path.basename(os.path.abspath(root))


def find_repo_root(start: str | None = None) -> str | None:
    """Walk up from start (or cwd) to find a directory containing .git."""
    current = Path(start or os.getcwd()).resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return str(parent)
    return str(current)  # No .git found, use cwd


def get_db_dir(repo_root: str | None = None) -> str:
    """Return the path to .claude/memory/ under the repo root."""
    root = repo_root or find_repo_root()
    return os.path.join(root, ".claude", "memory")


def get_db_path(repo_root: str | None = None) -> str:
    """Return the full path to the SQLite database file."""
    return os.path.join(get_db_dir(repo_root), DB_NAME)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Get a database connection with WAL mode for concurrency."""
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and FTS5 index if they don't exist. Idempotent."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS entries (
            id               TEXT PRIMARY KEY,
            project          TEXT NOT NULL DEFAULT 'global',
            tier             TEXT NOT NULL CHECK(tier IN ('L0', 'L1', 'L2')),
            category         TEXT NOT NULL,
            title            TEXT NOT NULL,
            content          TEXT NOT NULL,
            tags             TEXT DEFAULT '[]',
            status           TEXT NOT NULL DEFAULT 'active',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            expires_at       TEXT,
            session_id       TEXT,
            supersedes       TEXT,
            access_count     INTEGER DEFAULT 0,
            last_accessed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id           TEXT PRIMARY KEY,
            project      TEXT NOT NULL,
            started_at   TEXT NOT NULL,
            ended_at     TEXT,
            summary      TEXT,
            l0_tokens    INTEGER DEFAULT 0,
            l1_tokens    INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_entries_project_tier
            ON entries(project, tier, status);
        CREATE INDEX IF NOT EXISTS idx_entries_category
            ON entries(category, status);
        CREATE INDEX IF NOT EXISTS idx_entries_expires
            ON entries(expires_at) WHERE expires_at IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_entries_updated
            ON entries(updated_at DESC);

        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
            title, content, tags,
            content=entries, content_rowid=rowid
        );

        CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
            INSERT INTO entries_fts(rowid, title, content, tags)
            VALUES (new.rowid, new.title, new.content, new.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, title, content, tags)
            VALUES ('delete', old.rowid, old.title, old.content, old.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, title, content, tags)
            VALUES ('delete', old.rowid, old.title, old.content, old.tags);
            INSERT INTO entries_fts(rowid, title, content, tags)
            VALUES (new.rowid, new.title, new.content, new.tags);
        END;
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def make_id(project: str, category: str, title: str) -> str:
    """Deterministic ID from project + category + title. Enables upserts."""
    safe_title = redact_secrets(title)
    raw = f"{project}:{category}:{safe_title}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _sanitize_text(value: str | None) -> str | None:
    """Redact secrets before writing user-provided text to the store."""
    if value is None:
        return None
    return redact_secrets(value)


def _sanitize_tags(tags: list[str] | None) -> list[str]:
    """Redact secrets from tag values before storing them."""
    return [redact_secrets(str(tag)) for tag in (tags or [])]


def _record_access(conn: sqlite3.Connection, entries: list[dict]) -> None:
    """Track entry retrieval across the normal boot and query paths."""
    entry_ids = [entry["id"] for entry in entries if entry.get("id")]
    if not entry_ids:
        return

    now = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" * len(entry_ids))
    conn.execute(
        f"UPDATE entries SET access_count = access_count + 1, last_accessed_at = ? "
        f"WHERE id IN ({placeholders})",
        [now] + entry_ids,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def upsert_entry(
    conn: sqlite3.Connection,
    *,
    project: str = "global",
    tier: str,
    category: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
    status: str = STATUS_ACTIVE,
    expires_at: str | None = None,
    session_id: str | None = None,
    supersedes: str | None = None,
) -> str:
    """Insert or update an entry. Returns the entry ID."""
    safe_title = _sanitize_text(title) or ""
    safe_content = _sanitize_text(content) or ""
    entry_id = make_id(project, category, safe_title)
    now = datetime.now(timezone.utc).isoformat()
    tags_json = json.dumps(_sanitize_tags(tags))

    conn.execute("""
        INSERT INTO entries (id, project, tier, category, title, content, tags,
                            status, created_at, updated_at, expires_at,
                            session_id, supersedes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            content = excluded.content,
            tier = excluded.tier,
            tags = excluded.tags,
            status = excluded.status,
            updated_at = excluded.updated_at,
            expires_at = excluded.expires_at,
            session_id = excluded.session_id,
            supersedes = excluded.supersedes
    """, (entry_id, project, tier, category, safe_title, safe_content, tags_json,
          status, now, now, expires_at, session_id, supersedes))
    conn.commit()
    return entry_id


def get_entry(conn: sqlite3.Connection, entry_id: str) -> dict | None:
    """Get a single entry by ID. Increments access count."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE entries SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?",
        (now, entry_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row else None


def archive_entry(conn: sqlite3.Connection, entry_id: str) -> None:
    """Mark an entry as archived."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE entries SET status = 'archived', updated_at = ? WHERE id = ?", (now, entry_id))
    conn.commit()


def complete_entry(conn: sqlite3.Connection, entry_id: str) -> None:
    """Mark an entry as completed."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE entries SET status = 'completed', updated_at = ? WHERE id = ?", (now, entry_id))
    conn.commit()


def expire_stale_entries(conn: sqlite3.Connection) -> int:
    """Mark entries past their expires_at as expired. Returns count expired."""
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute("""
        UPDATE entries SET status = 'expired', updated_at = ?
        WHERE expires_at IS NOT NULL AND expires_at < ? AND status = 'active'
    """, (now, now))
    conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Tiered retrieval
# ---------------------------------------------------------------------------

def get_l0(conn: sqlite3.Connection, project: str | None = None) -> list[dict]:
    """L0: Always loaded. ~500 tokens. Identity, phase, top priorities."""
    clauses = ["tier = 'L0'", "status = 'active'"]
    params: list = []
    if project:
        clauses.append("(project = ? OR project = 'global')")
        params.append(project)
    query = f"SELECT * FROM entries WHERE {' AND '.join(clauses)} ORDER BY category, updated_at DESC"
    entries = [dict(r) for r in conn.execute(query, params).fetchall()]
    _record_access(conn, entries)
    return entries


def get_l1(conn: sqlite3.Connection, project: str | None = None, since_days: int = 7) -> list[dict]:
    """L1: Session-relevant. ~1500 tokens. Recent decisions, active items, blockers."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    clauses = ["tier = 'L1'", "status IN ('active', 'completed')", "updated_at > ?"]
    params: list = [cutoff]
    if project:
        clauses.append("(project = ? OR project = 'global')")
        params.append(project)
    query = f"""
        SELECT * FROM entries WHERE {' AND '.join(clauses)}
        ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, updated_at DESC
        LIMIT 30
    """
    entries = [dict(r) for r in conn.execute(query, params).fetchall()]
    _record_access(conn, entries)
    return entries


def get_l2(conn: sqlite3.Connection, project: str | None = None,
           category: str | None = None, search: str | None = None,
           limit: int = 20) -> list[dict]:
    """L2: On-demand deep retrieval. Supports category filter and FTS."""
    if search:
        return search_entries(conn, search, project=project, limit=limit)
    clauses = ["status != 'expired'"]
    params: list = []
    if project:
        clauses.append("(project = ? OR project = 'global')")
        params.append(project)
    if category:
        clauses.append("category = ?")
        params.append(category)
    query = f"SELECT * FROM entries WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    entries = [dict(r) for r in conn.execute(query, params).fetchall()]
    _record_access(conn, entries)
    return entries


# ---------------------------------------------------------------------------
# Full-text search
# ---------------------------------------------------------------------------

def search_entries(conn: sqlite3.Connection, query_text: str,
                   project: str | None = None, category: str | None = None,
                   limit: int = 20) -> list[dict]:
    """FTS5 search across title, content, and tags. BM25 ranked."""
    params: list = [query_text]
    joins = []
    if project:
        joins.append("e.project IN (?, 'global')")
        params.append(project)
    if category:
        joins.append("e.category = ?")
        params.append(category)
    where_extra = (" AND " + " AND ".join(joins)) if joins else ""
    query = f"""
        SELECT e.*, rank FROM entries_fts
        JOIN entries e ON entries_fts.rowid = e.rowid
        WHERE entries_fts MATCH ? AND e.status != 'expired'{where_extra}
        ORDER BY rank LIMIT ?
    """
    params.append(limit)
    try:
        entries = [dict(r) for r in conn.execute(query, params).fetchall()]
    except sqlite3.OperationalError:
        # FTS query syntax error — fall back to LIKE
        like = f"%{query_text}%"
        clauses = ["(title LIKE ? OR content LIKE ?)", "status != 'expired'"]
        fb_params: list = [like, like]
        if project:
            clauses.append("(project = ? OR project = 'global')")
            fb_params.append(project)
        if category:
            clauses.append("category = ?")
            fb_params.append(category)
        fb_params.append(limit)
        entries = [dict(r) for r in conn.execute(
            f"SELECT * FROM entries WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?",
            fb_params,
        ).fetchall()]
    _record_access(conn, entries)
    return entries


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def start_session(conn: sqlite3.Connection, session_id: str, project: str = "global") -> None:
    """Record the start of a new session."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, project, started_at) VALUES (?, ?, ?)",
        (session_id, project, now),
    )
    conn.commit()


def end_session(conn: sqlite3.Connection, session_id: str, summary: str | None = None,
                l0_tokens: int = 0, l1_tokens: int = 0, total_tokens: int = 0) -> None:
    """Record the end of a session with optional summary and token counts."""
    now = datetime.now(timezone.utc).isoformat()
    safe_summary = _sanitize_text(summary)
    conn.execute("""
        UPDATE sessions SET ended_at = ?, summary = ?,
            l0_tokens = ?, l1_tokens = ?, total_tokens = ?
        WHERE id = ?
    """, (now, safe_summary, l0_tokens, l1_tokens, total_tokens, session_id))
    conn.commit()


def get_last_session(conn: sqlite3.Connection, project: str | None = None) -> dict | None:
    """Get the most recent completed session."""
    if project:
        row = conn.execute(
            "SELECT * FROM sessions WHERE project = ? AND ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1",
            (project,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM sessions WHERE ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1",
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def get_stats(conn: sqlite3.Connection, project: str | None = None) -> dict:
    """Return summary statistics about the memory store."""
    clauses: list[str] = []
    params: list = []
    if project:
        clauses.append("(project = ? OR project = 'global')")
        params.append(project)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    stats: dict = {}
    for label, col in [("by_status", "status"), ("by_tier", "tier"), ("by_category", "category")]:
        rows = conn.execute(
            f"SELECT {col}, COUNT(*) as cnt FROM entries {where} GROUP BY {col}", params,
        ).fetchall()
        stats[label] = {r[col]: r["cnt"] for r in rows}

    row = conn.execute("SELECT COUNT(*) as cnt FROM sessions").fetchone()
    stats["total_sessions"] = row["cnt"]

    db_path = get_db_path()
    if os.path.exists(db_path):
        stats["db_size_kb"] = round(os.path.getsize(db_path) / 1024, 1)

    return stats


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_entry_compact(entry: dict) -> str:
    """Format a single entry as a compact string for context injection."""
    parts = [f"[{entry['category']}] {entry['title']}"]
    if entry.get("expires_at"):
        parts.append(f"(expires: {entry['expires_at'][:10]})")
    if entry.get("status") != "active":
        parts.append(f"({entry['status']})")
    parts.append(f"\n  {entry['content']}")
    return " ".join(parts)


def format_boot_context(l0_entries: list[dict], l1_entries: list[dict],
                        project: str | None = None) -> str:
    """Format L0 + L1 entries into a compact context briefing."""
    lines: list[str] = []
    lines.append(f"# Memory Briefing — {project or 'global'}")
    lines.append(f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    lines.append("")

    l0_text = ""
    if l0_entries:
        lines.append("## Core (always relevant)")
        for e in l0_entries:
            text = format_entry_compact(e)
            l0_text += text + "\n"
            lines.append(text)
        lines.append("")

    l1_text = ""
    if l1_entries:
        lines.append("## Recent (last 7 days)")
        by_cat: dict[str, list[dict]] = {}
        for e in l1_entries:
            by_cat.setdefault(e["category"], []).append(e)
        for cat, entries in by_cat.items():
            lines.append(f"### {cat}")
            for e in entries:
                text = format_entry_compact(e)
                l1_text += text + "\n"
                lines.append(text)
            lines.append("")

    # Token estimate footer
    l0_tokens = estimate_tokens(l0_text)
    l1_tokens = estimate_tokens(l1_text)
    full_text = "\n".join(lines)
    total_tokens = estimate_tokens(full_text)
    lines.append(f"# Boot tokens: ~{total_tokens} (L0: ~{l0_tokens}, L1: ~{l1_tokens})")

    return "\n".join(lines)
