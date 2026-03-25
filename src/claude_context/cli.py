"""Unified CLI entry point for claude-context.

Usage:
    claude-context init              # Bootstrap memory for current repo
    claude-context boot              # Print session briefing
    claude-context save add ...      # Save a context entry
    claude-context save complete ... # Mark entry completed
    claude-context save session-end  # Record session summary
    claude-context query "search"    # Deep search
    claude-context index             # Re-index codebase
    claude-context index find "X"    # Search codebase
    claude-context index map         # Show structure
    claude-context compact           # Manual compaction
    claude-context stats             # Show memory stats
"""

import sys


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _print_help()
        return

    command = sys.argv[1]
    rest = sys.argv[2:]

    if command == "init":
        from .init import main as init_main
        init_main(rest)

    elif command == "boot":
        from .boot import main as boot_main
        boot_main(rest)

    elif command == "save":
        from .save import main as save_main
        save_main(rest)

    elif command == "query":
        from .query import main as query_main
        query_main(rest)

    elif command == "index":
        from .index import main as index_main
        index_main(rest)

    elif command == "compact":
        from .compact import main as compact_main
        compact_main(rest)

    elif command == "stats":
        from .query import main as query_main
        query_main(["--stats"] + rest)

    elif command == "version":
        from . import __version__
        print(f"claude-context {__version__}")

    else:
        print(f"Unknown command: {command}")
        print()
        _print_help()
        sys.exit(1)


def _print_help():
    print("claude-context — One-command context memory for Claude Code")
    print()
    print("Commands:")
    print("  init              Bootstrap memory for the current repo")
    print("  boot              Print session briefing (~2000 tokens)")
    print("  save add ...      Save a context entry")
    print("  save complete ... Mark an entry as completed")
    print("  save session-end  Record end-of-session summary")
    print("  query \"search\"    Search memory (full-text)")
    print("  index             Re-index the codebase")
    print("  index find \"X\"    Search codebase files")
    print("  index map         Show structural overview")
    print("  compact           Run L1 compaction manually")
    print("  stats             Show memory store statistics")
    print("  version           Show version")
    print()
    print("Quick start:")
    print("  claude-context init    # Run once per project")
    print("  claude-context boot    # Run at start of every session")
