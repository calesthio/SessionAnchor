"""End-to-end CLI tests that exercise the README workflows."""

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


CLI = [sys.executable, "-m", "claude_context"]


def run_cli(repo: Path, *args: str, expect_code: int = 0) -> subprocess.CompletedProcess:
    """Run the CLI in a temp repo and assert the exit code by default."""
    result = subprocess.run(
        CLI + list(args),
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == expect_code, (
        f"command={args}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    return result


def parse_entry_id(output: str) -> str:
    """Extract the deterministic entry id from `save add` output."""
    match = re.search(r"OK:\s+([0-9a-f]{16})\s", output)
    assert match, output
    return match.group(1)


def open_db(repo: Path) -> sqlite3.Connection:
    """Open the repo's SQLite database with row access by name."""
    conn = sqlite3.connect(repo / ".claude" / "memory" / "context.db")
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a fake git repo with a small codebase and ignored secret file."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "import os\n\n\ndef login_user():\n    return 'auth success'\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "AUTH_SECRET=sk-abcdefghijklmnopqrstuvwxyz1234\n",
        encoding="utf-8",
    )
    return tmp_path


def test_save_and_session_end_redact_secrets(repo: Path):
    run_cli(repo, "init", "--skip-index")
    run_cli(
        repo,
        "save",
        "add",
        "--tier",
        "L1",
        "--category",
        "technical_note",
        "--title",
        "Rotate credentials",
        "--content",
        "Replace token sk-abcdefghijklmnopqrstuvwxyz1234 in staging",
        "--tags",
        '["auth","ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"]',
    )
    run_cli(
        repo,
        "save",
        "session-end",
        "--session-id",
        "sess-1",
        "--summary",
        "Rotated token sk-abcdefghijklmnopqrstuvwxyz1234",
    )

    conn = open_db(repo)
    entry = conn.execute(
        "SELECT content, tags FROM entries WHERE title = ?",
        ("Rotate credentials",),
    ).fetchone()
    session = conn.execute("SELECT summary FROM sessions WHERE id = 'sess-1'").fetchone()
    conn.close()

    assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in entry["content"]
    assert "[REDACTED]" in entry["content"]
    assert json.loads(entry["tags"]) == ["auth", "[REDACTED]"]
    assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in session["summary"]
    assert "[REDACTED]" in session["summary"]


def test_boot_and_query_update_access_tracking(repo: Path):
    run_cli(repo, "init", "--skip-index")
    run_cli(
        repo,
        "save",
        "add",
        "--tier",
        "L0",
        "--category",
        "identity",
        "--title",
        "Project identity",
        "--content",
        "CLI memory for auth project",
    )
    run_cli(
        repo,
        "save",
        "add",
        "--tier",
        "L1",
        "--category",
        "decision",
        "--title",
        "Use SQLite",
        "--content",
        "SQLite keeps the tool zero-dependency",
    )

    query_result = run_cli(repo, "query", "SQLite")
    boot_result = run_cli(repo, "boot")

    conn = open_db(repo)
    row = conn.execute(
        "SELECT access_count, last_accessed_at FROM entries WHERE title = ?",
        ("Use SQLite",),
    ).fetchone()
    conn.close()

    assert "# 1 entries found" in query_result.stdout
    assert "## Recent (last 7 days)" in boot_result.stdout
    assert "Boot tokens:" in boot_result.stdout
    assert row["access_count"] >= 2
    assert row["last_accessed_at"] is not None


def test_compact_archives_superseded_entries(repo: Path):
    run_cli(repo, "init", "--skip-index")
    old_result = run_cli(
        repo,
        "save",
        "add",
        "--tier",
        "L1",
        "--category",
        "decision",
        "--title",
        "Legacy auth flow",
        "--content",
        "Keep the legacy callback flow",
    )
    old_id = parse_entry_id(old_result.stdout)
    run_cli(
        repo,
        "save",
        "add",
        "--tier",
        "L1",
        "--category",
        "decision",
        "--title",
        "OAuth PKCE flow",
        "--content",
        "Use PKCE instead of the legacy callback flow",
        "--supersedes",
        old_id,
    )
    compact_result = run_cli(repo, "compact")

    conn = open_db(repo)
    rows = conn.execute(
        "SELECT title, status FROM entries WHERE title IN (?, ?) ORDER BY title",
        ("Legacy auth flow", "OAuth PKCE flow"),
    ).fetchall()
    conn.close()

    statuses = {row["title"]: row["status"] for row in rows}
    assert "superseded_archived: 1" in compact_result.stdout
    assert statuses["Legacy auth flow"] == "archived"
    assert statuses["OAuth PKCE flow"] == "active"


def test_boot_full_includes_last_session_summary(repo: Path):
    run_cli(repo, "init", "--skip-index")
    run_cli(
        repo,
        "save",
        "add",
        "--tier",
        "L0",
        "--category",
        "identity",
        "--title",
        "Project identity",
        "--content",
        "CLI memory for auth project",
    )
    run_cli(
        repo,
        "save",
        "session-end",
        "--session-id",
        "sess-1",
        "--summary",
        "Shipped auth flow",
        "--l0-tokens",
        "10",
        "--l1-tokens",
        "20",
    )

    result = run_cli(repo, "boot", "--full")

    assert "## Last Session" in result.stdout
    assert "Summary: Shipped auth flow" in result.stdout
    assert "Tokens loaded: L0=10 L1=20 total=30" in result.stdout


def test_save_lifecycle_commands_and_stats_alias(repo: Path):
    run_cli(repo, "init", "--skip-index")
    bulk_file = repo / "entries.json"
    bulk_file.write_text(
        json.dumps(
            [
                {
                    "tier": "L2",
                    "category": "technical_note",
                    "title": "Imported note",
                    "content": "Deep implementation note",
                },
                {
                    "tier": "L1",
                    "category": "action_item",
                    "title": "Ship auth",
                    "content": "Finish the auth rollout",
                },
            ]
        ),
        encoding="utf-8",
    )

    run_cli(repo, "save", "bulk", "--file", "entries.json")
    run_cli(
        repo,
        "save",
        "promote",
        "--title",
        "Imported note",
        "--category",
        "technical_note",
        "--new-tier",
        "L1",
    )
    run_cli(
        repo,
        "save",
        "complete",
        "--title",
        "Ship auth",
        "--category",
        "action_item",
    )
    run_cli(
        repo,
        "save",
        "archive",
        "--title",
        "Imported note",
        "--category",
        "technical_note",
    )

    query_result = run_cli(
        repo,
        "query",
        "--category",
        "action_item",
        "--status",
        "completed",
    )
    stats_result = run_cli(repo, "stats")

    conn = open_db(repo)
    rows = conn.execute(
        "SELECT title, tier, status FROM entries WHERE title IN (?, ?)",
        ("Imported note", "Ship auth"),
    ).fetchall()
    conn.close()

    stats = json.loads(stats_result.stdout)
    by_title = {row["title"]: (row["tier"], row["status"]) for row in rows}

    assert "Ship auth" in query_result.stdout
    assert by_title["Imported note"] == ("L1", "archived")
    assert by_title["Ship auth"] == ("L1", "completed")
    assert stats["by_status"]["archived"] >= 1
    assert stats["by_status"]["completed"] >= 1


def test_index_find_and_map_cover_repo_workflow(repo: Path):
    run_cli(repo, "init", "--skip-index")

    index_result = run_cli(repo, "index")
    find_result = run_cli(repo, "index", "find", "auth", "--repo-root", ".")
    map_result = run_cli(repo, "index", "map")

    conn = open_db(repo)
    indexed_entries = conn.execute(
        "SELECT COUNT(*) AS cnt FROM entries WHERE tags LIKE '%code-index%'"
    ).fetchone()
    conn.close()

    assert "Indexed" in index_result.stdout
    assert "src\\auth.py" in find_result.stdout
    assert ".env" not in find_result.stdout
    assert "## Modules" in map_result.stdout
    assert "## File Tree" in map_result.stdout
    assert indexed_entries["cnt"] > 0
