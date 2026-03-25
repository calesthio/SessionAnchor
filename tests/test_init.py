"""Tests for the init command."""

import os
import tempfile
import shutil

import pytest

from sessionanchor.init import run_init


@pytest.fixture
def temp_repo():
    """Create a temporary directory simulating a git repo."""
    tmpdir = tempfile.mkdtemp()
    # Create a fake .git directory
    os.makedirs(os.path.join(tmpdir, ".git"))
    # Create a simple source file
    os.makedirs(os.path.join(tmpdir, "src"))
    with open(os.path.join(tmpdir, "src", "main.py"), "w") as f:
        f.write("def main():\n    print('hello')\n")
    yield tmpdir
    shutil.rmtree(tmpdir)


def test_init_creates_memory_dir(temp_repo):
    run_init(repo_root=temp_repo, skip_index=True)
    assert os.path.isdir(os.path.join(temp_repo, ".claude", "memory"))


def test_init_creates_db(temp_repo):
    run_init(repo_root=temp_repo, skip_index=True)
    assert os.path.isfile(os.path.join(temp_repo, ".claude", "memory", "context.db"))


def test_init_creates_contextignore(temp_repo):
    run_init(repo_root=temp_repo, skip_index=True)
    assert os.path.isfile(os.path.join(temp_repo, ".contextignore"))


def test_init_creates_claude_md(temp_repo):
    run_init(repo_root=temp_repo, skip_index=True)
    claude_md = os.path.join(temp_repo, "CLAUDE.md")
    assert os.path.isfile(claude_md)
    with open(claude_md) as f:
        content = f.read()
    assert "## Context Management" in content
    assert "sessionanchor boot" in content


def test_claude_md_has_mandatory_sections(temp_repo):
    run_init(repo_root=temp_repo, skip_index=True)
    claude_md = os.path.join(temp_repo, "CLAUDE.md")
    with open(claude_md) as f:
        content = f.read()
    # Mandatory start protocol
    assert "MANDATORY: Session Start" in content
    # Mandatory save protocol
    assert "MANDATORY: Save After Every Substantive Action" in content
    # Self-check instruction
    assert "Self-check" in content
    # Install fallback
    assert "pip install sessionanchor" in content
    # python -m fallback
    assert "python -m sessionanchor" in content
    # runner fallback for ephemeral installs
    assert "uvx sessionanchor" in content
    # completion wording should stay accurate
    assert "marks the action item as completed" in content
    assert "leaves the boot briefing" not in content


def test_init_warns_when_cli_not_on_path(temp_repo, capsys, monkeypatch):
    import sessionanchor.init as init_module
    monkeypatch.setattr(init_module.shutil, "which", lambda _name: None)
    run_init(repo_root=temp_repo, skip_index=True)
    captured = capsys.readouterr()
    assert "not found on PATH" in captured.out
    assert "python -m sessionanchor" in captured.out
    assert "<launcher> sessionanchor" in captured.out


def test_init_patches_existing_claude_md(temp_repo):
    claude_md = os.path.join(temp_repo, "CLAUDE.md")
    with open(claude_md, "w") as f:
        f.write("# My Project\n\nExisting content.\n")

    run_init(repo_root=temp_repo, skip_index=True)

    with open(claude_md) as f:
        content = f.read()
    assert "Existing content." in content
    assert "## Context Management" in content


def test_init_idempotent(temp_repo):
    run_init(repo_root=temp_repo, skip_index=True)
    run_init(repo_root=temp_repo, skip_index=True)
    # Should not duplicate the context management section
    with open(os.path.join(temp_repo, "CLAUDE.md")) as f:
        content = f.read()
    assert content.count("## Context Management") == 1


def test_init_updates_gitignore(temp_repo):
    run_init(repo_root=temp_repo, skip_index=True)
    gitignore = os.path.join(temp_repo, ".gitignore")
    assert os.path.isfile(gitignore)
    with open(gitignore) as f:
        content = f.read()
    assert ".claude/memory/" in content


def test_init_with_index(temp_repo):
    run_init(repo_root=temp_repo, skip_index=False)
    # Should have indexed the src/main.py file
    import sqlite3
    db_path = os.path.join(temp_repo, ".claude", "memory", "context.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT COUNT(*) as cnt FROM entries").fetchone()
    assert rows["cnt"] > 0
    conn.close()
