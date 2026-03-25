"""Init command — one-command bootstrap for context memory.

`claude-context init` does:
1. Detect repo root
2. Create .claude/memory/ directory
3. Create empty SQLite database with schema
4. Create .contextignore with sensible defaults
5. Create or patch CLAUDE.md
6. Run initial repo index
7. Print "how to use" block
"""

import os
import shutil
import sys
from pathlib import Path

from .index import build_index
from .store import (
    DB_NAME,
    detect_project_name,
    find_repo_root,
    get_connection,
    init_db,
    upsert_entry,
)

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

CONTEXT_SECTION_MARKER = "## Context Management"


def run_init(repo_root: str | None = None, skip_index: bool = False) -> None:
    """Run the full init sequence."""
    root = repo_root or find_repo_root()
    project = detect_project_name(root)

    print(f"Initializing claude-context for: {project}")
    print(f"  Repo root: {root}")
    print()

    # 1. Create .claude/memory/
    memory_dir = os.path.join(root, ".claude", "memory")
    os.makedirs(memory_dir, exist_ok=True)
    print(f"  [+] Created {os.path.relpath(memory_dir, root)}/")

    # 2. Create SQLite database
    db_path = os.path.join(memory_dir, DB_NAME)
    conn = get_connection(db_path)
    init_db(conn)
    print(f"  [+] Created {os.path.relpath(db_path, root)}")

    # 3. Create .contextignore
    ignore_path = os.path.join(root, ".contextignore")
    if not os.path.exists(ignore_path):
        template = os.path.join(TEMPLATES_DIR, "contextignore")
        shutil.copy2(template, ignore_path)
        print(f"  [+] Created .contextignore")
    else:
        print(f"  [=] .contextignore already exists, skipping")

    # 4. Create or patch CLAUDE.md
    claude_md_path = os.path.join(root, "CLAUDE.md")
    section_template = os.path.join(TEMPLATES_DIR, "claude_md_section.txt")
    with open(section_template, "r", encoding="utf-8") as f:
        section_content = f.read()

    if not os.path.exists(claude_md_path):
        with open(claude_md_path, "w", encoding="utf-8") as f:
            f.write(f"# {project} — Claude Code Instructions\n")
            f.write(section_content)
        print(f"  [+] Created CLAUDE.md")
    else:
        with open(claude_md_path, "r", encoding="utf-8") as f:
            existing = f.read()
        if CONTEXT_SECTION_MARKER in existing:
            print(f"  [=] CLAUDE.md already has context management section, skipping")
        else:
            with open(claude_md_path, "a", encoding="utf-8") as f:
                f.write("\n")
                f.write(section_content)
            print(f"  [+] Patched CLAUDE.md with context management section")

    # 5. Ensure .claude/memory/ is in .gitignore
    _ensure_gitignore(root)

    # 6. Run initial repo index
    if not skip_index:
        print()
        print("  Indexing repository structure...")
        entries = build_index(root, project=project)
        for e in entries:
            upsert_entry(conn, **e)
        print(f"  [+] Indexed {len(entries)} entries")

    conn.close()

    # 7. Print how-to
    print()
    print("=" * 60)
    print("  claude-context initialized!")
    print("=" * 60)
    print()
    print("  Quick start:")
    print()
    print("  # At session start - get your briefing")
    print("  claude-context boot")
    print()
    print("  # Save a decision")
    print('  claude-context save add --tier L1 --category decision \\')
    print('    --title "Chose SQLite" --content "Zero deps, local-only"')
    print()
    print("  # Search memory")
    print('  claude-context query "deployment"')
    print()
    print("  # End of session")
    print('  claude-context save session-end --summary "Built auth flow"')
    print()
    print("  CLAUDE.md has been configured to instruct Claude to use")
    print("  this memory system automatically.")
    print()


def _ensure_gitignore(root: str) -> None:
    """Add .claude/memory/ to .gitignore if not already present."""
    gitignore_path = os.path.join(root, ".gitignore")
    memory_pattern = ".claude/memory/"

    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if memory_pattern in content:
            return
        with open(gitignore_path, "a", encoding="utf-8") as f:
            f.write(f"\n# Claude context memory (local-only)\n{memory_pattern}\n")
        print(f"  [+] Added {memory_pattern} to .gitignore")
    else:
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write(f"# Claude context memory (local-only)\n{memory_pattern}\n")
        print(f"  [+] Created .gitignore with {memory_pattern}")


def main(args=None):
    """CLI entry point for init command."""
    import argparse

    parser = argparse.ArgumentParser(description="Initialize claude-context")
    parser.add_argument("--repo-root", default=None, help="Path to repo root")
    parser.add_argument("--skip-index", action="store_true", help="Skip initial repo index")
    parsed = parser.parse_args(args)

    run_init(repo_root=parsed.repo_root, skip_index=parsed.skip_index)
