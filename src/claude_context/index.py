"""Repo structure indexer — lightweight code analysis without AST parsing.

Crawls the repo, builds module summaries, and stores them as memory entries.
Respects .contextignore patterns. Supports JS/TS, Python, SQL, and YAML files.
"""

import json
import os
import re
from pathlib import Path

from .ignore import load_patterns, redact_secrets, should_ignore
from .store import (
    TIER_L1,
    TIER_L2,
    detect_project_name,
    get_connection,
    get_db_path,
    init_db,
    upsert_entry,
)

CODE_EXTENSIONS = {
    ".mjs", ".js", ".ts", ".jsx", ".tsx", ".py", ".sql", ".sh", ".yml", ".yaml",
}

CONFIG_FILES = {
    "package.json", "vercel.json", "tsconfig.json", ".env.example",
    "wrangler.toml", "Dockerfile", "docker-compose.yml", "Makefile",
    "pyproject.toml", "setup.py", "setup.cfg", "Cargo.toml", "go.mod",
}


def discover_files(repo_root: str, patterns: list[str] | None = None):
    """Walk the repo and categorize files, respecting ignore patterns."""
    if patterns is None:
        patterns = load_patterns(repo_root)

    code_files = []
    config_files = []
    workflow_files = []

    for root, dirs, files in os.walk(repo_root):
        rel_root = os.path.relpath(root, repo_root)

        # Prune ignored directories
        dirs[:] = [d for d in dirs if not should_ignore(
            os.path.join(rel_root, d) if rel_root != "." else d, patterns
        )]

        for f in files:
            rel_path = os.path.join(rel_root, f) if rel_root != "." else f
            full_path = os.path.join(root, f)
            ext = os.path.splitext(f)[1].lower()

            if should_ignore(rel_path, patterns):
                continue

            if "workflows" in rel_path and ext in (".yml", ".yaml"):
                workflow_files.append((rel_path, full_path))
            elif f in CONFIG_FILES:
                config_files.append((rel_path, full_path))
            elif ext in CODE_EXTENSIONS:
                code_files.append((rel_path, full_path))

    return code_files, config_files, workflow_files


# ---------------------------------------------------------------------------
# File analyzers (regex-based, no AST)
# ---------------------------------------------------------------------------

def _read_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def analyze_js_file(full_path: str, rel_path: str) -> dict | None:
    """Extract exports, imports, key patterns from JS/TS files."""
    content = _read_file(full_path)
    if not content:
        return None

    info = {
        "path": rel_path,
        "lines": content.count("\n") + 1,
        "exports": [],
        "imports": [],
        "functions": [],
        "classes": [],
        "api_routes": [],
    }

    for m in re.finditer(r"export\s+(?:async\s+)?function\s+(\w+)", content):
        info["exports"].append(m.group(1))
    for m in re.finditer(r"export\s+(?:const|let|var)\s+(\w+)", content):
        info["exports"].append(m.group(1))
    for m in re.finditer(r"export\s+default\s+(?:async\s+)?function\s+(\w+)", content):
        info["exports"].append(f"{m.group(1)} (default)")
    for m in re.finditer(r"module\.exports\s*=\s*\{([^}]+)\}", content):
        info["exports"].extend(re.findall(r"(\w+)", m.group(1)))
    for m in re.finditer(r"import\s+.*?from\s+['\"]([^'\"]+)['\"]", content):
        info["imports"].append(m.group(1))
    for m in re.finditer(r"require\(['\"]([^'\"]+)['\"]\)", content):
        info["imports"].append(m.group(1))
    for m in re.finditer(r"(?:async\s+)?function\s+(\w+)", content):
        if m.group(1) not in info["exports"]:
            info["functions"].append(m.group(1))
    for m in re.finditer(r"class\s+(\w+)", content):
        info["classes"].append(m.group(1))
    for m in re.finditer(r"app\.(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)", content):
        info["api_routes"].append(f"{m.group(1).upper()} {m.group(2)}")

    return info


def analyze_python_file(full_path: str, rel_path: str) -> dict | None:
    """Extract classes, functions, imports from Python files."""
    content = _read_file(full_path)
    if not content:
        return None

    info = {
        "path": rel_path,
        "lines": content.count("\n") + 1,
        "exports": [],
        "imports": [],
        "functions": [],
        "classes": [],
    }

    for m in re.finditer(r"^class\s+(\w+)", content, re.MULTILINE):
        info["classes"].append(m.group(1))
    for m in re.finditer(r"^def\s+(\w+)", content, re.MULTILINE):
        if not m.group(1).startswith("_"):
            info["functions"].append(m.group(1))
    for m in re.finditer(r"^(?:from|import)\s+(\S+)", content, re.MULTILINE):
        info["imports"].append(m.group(1))

    # __all__ defines public API
    all_match = re.search(r"__all__\s*=\s*\[([^\]]+)\]", content)
    if all_match:
        info["exports"] = re.findall(r"['\"](\w+)['\"]", all_match.group(1))
    else:
        info["exports"] = info["classes"] + [f for f in info["functions"] if not f.startswith("_")]

    return info


def analyze_sql_file(full_path: str, rel_path: str) -> dict | None:
    """Extract table names, functions from SQL files."""
    content = _read_file(full_path)
    if not content:
        return None

    info = {
        "path": rel_path,
        "lines": content.count("\n") + 1,
        "tables": [],
        "functions": [],
    }

    for m in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", content, re.I):
        info["tables"].append(m.group(1))
    for m in re.finditer(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(\w+)", content, re.I):
        info["functions"].append(m.group(1))

    return info


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------

def build_index(repo_root: str, project: str | None = None) -> list[dict]:
    """Analyze the repo and return structured entries for the memory store."""
    project = project or detect_project_name(repo_root)
    patterns = load_patterns(repo_root)
    code_files, config_files, workflow_files = discover_files(repo_root, patterns)
    entries: list[dict] = []

    # Group code files by top-level module directory
    modules: dict[str, list] = {}
    for rel_path, full_path in code_files:
        parts = Path(rel_path).parts
        module_key = f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else (parts[0] if parts else "root")
        modules.setdefault(module_key, []).append((rel_path, full_path))

    # Per-module summaries
    for module_key, files in sorted(modules.items()):
        all_exports = []
        all_functions = []
        all_routes = []
        total_lines = 0

        for rel_path, full_path in files:
            ext = os.path.splitext(rel_path)[1].lower()
            if ext in (".mjs", ".js", ".ts", ".jsx", ".tsx"):
                info = analyze_js_file(full_path, rel_path)
            elif ext == ".py":
                info = analyze_python_file(full_path, rel_path)
            elif ext == ".sql":
                info = analyze_sql_file(full_path, rel_path)
            else:
                info = None

            if info:
                all_exports.extend(info.get("exports", [])[:10])
                all_functions.extend(info.get("functions", [])[:5])
                all_routes.extend(info.get("api_routes", []))
                total_lines += info.get("lines", 0)

        summary_parts = [f"{len(files)} files, ~{total_lines} lines"]
        if all_exports:
            summary_parts.append(f"Exports: {', '.join(all_exports[:15])}")
        if all_routes:
            summary_parts.append(f"Routes: {', '.join(all_routes[:10])}")
        if all_functions:
            summary_parts.append(f"Key functions: {', '.join(all_functions[:10])}")

        entries.append({
            "project": project, "tier": TIER_L1, "category": "architecture",
            "title": f"Module: {module_key}",
            "content": redact_secrets(". ".join(summary_parts)),
            "tags": ["code-index", "module"],
        })

    # File tree (compact overview)
    tree_lines = []
    for module_key in sorted(modules.keys()):
        tree_lines.append(f"{module_key}/ ({len(modules[module_key])} files)")
    for rel_path, _ in workflow_files:
        tree_lines.append(rel_path)

    entries.append({
        "project": project, "tier": TIER_L1, "category": "architecture",
        "title": "Repository file tree",
        "content": "\n".join(tree_lines),
        "tags": ["code-index", "structure"],
    })

    # Overall stats
    total_code = len(code_files)
    total_lines = 0
    for _, fp in code_files:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                total_lines += sum(1 for _ in f)
        except Exception:
            pass

    entries.append({
        "project": project, "tier": TIER_L1, "category": "architecture",
        "title": "Codebase stats",
        "content": (
            f"{total_code} code files, ~{total_lines} total lines. "
            f"{len(workflow_files)} workflows, {len(config_files)} config files."
        ),
        "tags": ["code-index", "stats"],
    })

    return entries


# ---------------------------------------------------------------------------
# Find — search codebase for a topic
# ---------------------------------------------------------------------------

def find_in_codebase(repo_root: str, query: str, show_code: bool = False,
                     file_type: str | None = None) -> list[dict]:
    """Search the codebase for a query string. Returns matching files with context."""
    patterns = load_patterns(repo_root)
    code_files, config_files, workflow_files = discover_files(repo_root, patterns)

    all_files = code_files + config_files + workflow_files
    if file_type == "module":
        all_files = code_files
    elif file_type == "workflow":
        all_files = workflow_files
    elif file_type == "config":
        all_files = config_files

    query_terms = query.lower().split()
    results = []

    for rel_path, full_path in all_files:
        content = _read_file(full_path)
        if not content:
            continue

        content_lower = content.lower()
        rel_lower = rel_path.lower()

        score = sum(3 if t in rel_lower else 0 for t in query_terms)
        score += sum(1 if t in content_lower else 0 for t in query_terms)

        if score == 0:
            continue

        matching_lines = []
        for i, line in enumerate(content.split("\n"), 1):
            if any(t in line.lower() for t in query_terms):
                matching_lines.append((i, line.strip()))

        result = {
            "path": rel_path,
            "score": score,
            "matching_lines": matching_lines[:10],
            "total_matches": len(matching_lines),
        }

        if show_code and matching_lines:
            lines = content.split("\n")
            snippets = []
            for line_no, _ in matching_lines[:3]:
                start = max(0, line_no - 3)
                end = min(len(lines), line_no + 3)
                snippet = "\n".join(
                    f"  {start+j+1:4d} | {lines[start+j]}" for j in range(end - start)
                )
                snippets.append(snippet)
            result["code_snippets"] = snippets

        results.append(result)

    results.sort(key=lambda r: -r["score"])
    return results[:20]


# ---------------------------------------------------------------------------
# Map — show the structural overview from memory
# ---------------------------------------------------------------------------

def show_map(conn, project: str) -> str:
    """Display the indexed structural map from the memory store."""
    params: list = ["%code-index%"]
    clauses = ["category = 'architecture'", "tags LIKE ?"]
    if project:
        clauses.append("(project = ? OR project = 'global')")
        params.append(project)
    entries = [
        dict(row) for row in conn.execute(
            f"SELECT * FROM entries WHERE {' AND '.join(clauses)} ORDER BY title",
            params,
        ).fetchall()
    ]
    if not entries:
        return "No code index found. Run `claude-context index` first."

    lines = []
    by_tag: dict[str, list] = {}
    for e in entries:
        tags = json.loads(e["tags"]) if isinstance(e["tags"], str) else e["tags"]
        key = "module" if "module" in tags else "structure" if "structure" in tags else "other"
        by_tag.setdefault(key, []).append(e)

    if "module" in by_tag:
        lines.append("## Modules")
        for e in sorted(by_tag["module"], key=lambda x: x["title"]):
            lines.append(f"  {e['title']}")
            lines.append(f"    {e['content'][:120]}")
            lines.append("")

    for e in entries:
        if e["title"] == "Codebase stats":
            lines.append(f"\n## Stats\n  {e['content']}")
        if e["title"] == "Repository file tree":
            lines.append(f"\n## File Tree\n{e['content']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(args=None):
    """CLI entry point for index command."""
    import argparse

    parser = argparse.ArgumentParser(description="Code index and search")
    parser.add_argument("--db", default=None)
    sub = parser.add_subparsers(dest="command")

    p_idx = sub.add_parser("run", help="Index the repository")
    p_idx.add_argument("--repo-root", default=".")
    p_idx.add_argument("--dry-run", action="store_true")
    p_idx.add_argument("--project", default=None)

    p_find = sub.add_parser("find", help="Search the codebase")
    p_find.add_argument("query")
    p_find.add_argument("--repo-root", default=".")
    p_find.add_argument("--type", choices=["module", "workflow", "config"], default=None)
    p_find.add_argument("--show-code", action="store_true")

    p_map = sub.add_parser("map", help="Show structural overview")
    p_map.add_argument("--project", default=None)

    parsed = parser.parse_args(args)

    if parsed.command == "run":
        project = parsed.project or detect_project_name(parsed.repo_root)
        entries = build_index(parsed.repo_root, project=project)
        if parsed.dry_run:
            print(f"Would index {len(entries)} entries:")
            for e in entries:
                print(f"  [{e['tier']}] {e['title']}")
            return

        db_path = parsed.db or get_db_path()
        conn = get_connection(db_path)
        init_db(conn)
        for e in entries:
            upsert_entry(conn, **e)
        conn.close()
        print(f"Indexed {len(entries)} entries into memory.")

    elif parsed.command == "find":
        results = find_in_codebase(parsed.repo_root, parsed.query,
                                   show_code=parsed.show_code, file_type=parsed.type)
        if not results:
            print("No matches found.")
            return
        print(f"# {len(results)} matches for '{parsed.query}'\n")
        for r in results:
            print(f"  {r['path']} (score: {r['score']}, {r['total_matches']} matches)")
            for line_no, line in r["matching_lines"][:3]:
                print(f"    L{line_no}: {line[:120]}")
            if r.get("code_snippets"):
                for snippet in r["code_snippets"]:
                    print(snippet)
                    print()
            print()

    elif parsed.command == "map":
        db_path = parsed.db or get_db_path()
        if not os.path.exists(db_path):
            print("No memory database found. Run `claude-context init` first.")
            return
        conn = get_connection(db_path)
        init_db(conn)
        project = parsed.project or detect_project_name()
        print(show_map(conn, project))
        conn.close()

    else:
        # Default: run index
        project = detect_project_name()
        entries = build_index(".", project=project)
        db_path = parsed.db if hasattr(parsed, "db") and parsed.db else get_db_path()
        conn = get_connection(db_path)
        init_db(conn)
        for e in entries:
            upsert_entry(conn, **e)
        conn.close()
        print(f"Indexed {len(entries)} entries into memory.")
