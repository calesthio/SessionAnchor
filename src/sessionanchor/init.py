"""Init command for SessionAnchor."""

import os
import shutil

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

    print(f"Initializing SessionAnchor for: {project}")
    print(f"  Repo root: {root}")
    print()

    memory_dir = os.path.join(root, ".claude", "memory")
    os.makedirs(memory_dir, exist_ok=True)
    print(f"  [+] Created {os.path.relpath(memory_dir, root)}/")

    db_path = os.path.join(memory_dir, DB_NAME)
    conn = get_connection(db_path)
    init_db(conn)
    print(f"  [+] Created {os.path.relpath(db_path, root)}")

    ignore_path = os.path.join(root, ".contextignore")
    if not os.path.exists(ignore_path):
        template = os.path.join(TEMPLATES_DIR, "contextignore")
        shutil.copy2(template, ignore_path)
        print("  [+] Created .contextignore")
    else:
        print("  [=] .contextignore already exists, skipping")

    claude_md_path = os.path.join(root, "CLAUDE.md")
    section_template = os.path.join(TEMPLATES_DIR, "claude_md_section.txt")
    with open(section_template, "r", encoding="utf-8") as f:
        section_content = f.read()

    if not os.path.exists(claude_md_path):
        with open(claude_md_path, "w", encoding="utf-8") as f:
            f.write(f"# {project} - Claude Code Instructions\n")
            f.write(section_content)
        print("  [+] Created CLAUDE.md")
    else:
        with open(claude_md_path, "r", encoding="utf-8") as f:
            existing = f.read()
        if CONTEXT_SECTION_MARKER in existing:
            print("  [=] CLAUDE.md already has context management section, skipping")
        else:
            with open(claude_md_path, "a", encoding="utf-8") as f:
                f.write("\n")
                f.write(section_content)
            print("  [+] Patched CLAUDE.md with context management section")

    _ensure_gitignore(root)

    if not skip_index:
        print()
        print("  Indexing repository structure...")
        entries = build_index(root, project=project)
        for entry in entries:
            upsert_entry(conn, **entry)
        print(f"  [+] Indexed {len(entries)} entries")

    conn.close()

    # Verify CLI is discoverable on PATH
    cli_on_path = shutil.which("sessionanchor") is not None
    quick_start_prefix = "sessionanchor"

    print()
    print("=" * 60)
    print("  SessionAnchor initialized!")
    print("=" * 60)

    if not cli_on_path:
        quick_start_prefix = "<launcher> sessionanchor"
        print()
        print("  [!] NOTE: 'sessionanchor' was not found on PATH.")
        print("      Use the same launcher for future commands, e.g.")
        print("      'uvx sessionanchor ...', 'pipx run sessionanchor ...',")
        print("      or 'python -m sessionanchor ...'.")
        print("      Replace <launcher> in the examples below with the one you use.")

    print()
    print("  Quick start:")
    print()
    print("  # At session start - get your briefing")
    print(f"  {quick_start_prefix} boot")
    print()
    print("  # Save a decision")
    print(f'  {quick_start_prefix} save add --tier L1 --category decision \\')
    print('    --title "Chose SQLite" --content "Zero deps, local-only"')
    print()
    print("  # Search memory")
    print(f'  {quick_start_prefix} query "deployment"')
    print()
    print("  # End of session")
    print(f'  {quick_start_prefix} save session-end --summary "Built auth flow"')
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
            f.write(f"\n# SessionAnchor memory (local-only)\n{memory_pattern}\n")
        print(f"  [+] Added {memory_pattern} to .gitignore")
    else:
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write(f"# SessionAnchor memory (local-only)\n{memory_pattern}\n")
        print(f"  [+] Created .gitignore with {memory_pattern}")


def main(args=None):
    """CLI entry point for init command."""
    import argparse

    parser = argparse.ArgumentParser(description="Initialize SessionAnchor")
    parser.add_argument("--repo-root", default=None, help="Path to repo root")
    parser.add_argument("--skip-index", action="store_true", help="Skip initial repo index")
    parsed = parser.parse_args(args)

    run_init(repo_root=parsed.repo_root, skip_index=parsed.skip_index)
