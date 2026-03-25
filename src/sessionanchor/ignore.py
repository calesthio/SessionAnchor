"""Parser for .contextignore files.

Gitignore-style pattern matching to exclude sensitive files from indexing
and redact secret-like content from stored entries.
"""

import os
import re
from fnmatch import fnmatch
from pathlib import Path

# Patterns that indicate secrets in file content
SECRET_PATTERNS = re.compile(
    r"(?:"
    r"AKIA[0-9A-Z]{16}"          # AWS access key
    r"|sk-[a-zA-Z0-9]{20,}"      # OpenAI / Stripe secret key
    r"|ghp_[a-zA-Z0-9]{36}"      # GitHub personal access token
    r"|gho_[a-zA-Z0-9]{36}"      # GitHub OAuth token
    r"|glpat-[a-zA-Z0-9\-]{20}"  # GitLab PAT
    r"|xox[bsarp]-[a-zA-Z0-9\-]+" # Slack token
    r"|Bearer\s+[a-zA-Z0-9\-._~+/]+=*"  # Bearer token
    r"|eyJ[a-zA-Z0-9\-_]+\.eyJ"  # JWT
    r")",
    re.ASCII,
)

DEFAULT_PATTERNS = """\
# Secrets & credentials
.env
.env.*
*.key
*.pem
*.p12
*.pfx

# Build artifacts
node_modules/
dist/
build/
.next/
__pycache__/
*.pyc
coverage/
.vercel/
vendor/

# Large/binary files
*.sqlite
*.db
*.wasm
*.zip
*.tar.gz
*.jar

# Claude's own memory
.claude/memory/
"""


def load_patterns(repo_root: str) -> list[str]:
    """Load ignore patterns from .contextignore, falling back to defaults."""
    ignore_path = os.path.join(repo_root, ".contextignore")
    if os.path.isfile(ignore_path):
        with open(ignore_path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    else:
        raw = DEFAULT_PATTERNS

    patterns = []
    for line in raw.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def should_ignore(rel_path: str, patterns: list[str]) -> bool:
    """Check if a relative path matches any ignore pattern."""
    parts = Path(rel_path).parts
    for pattern in patterns:
        # Directory pattern (ends with /)
        if pattern.endswith("/"):
            dir_name = pattern.rstrip("/")
            if dir_name in parts:
                return True
        # File/glob pattern
        else:
            if fnmatch(os.path.basename(rel_path), pattern):
                return True
            if fnmatch(rel_path, pattern):
                return True
    return False


def redact_secrets(text: str) -> str:
    """Replace detected secret patterns with [REDACTED]."""
    return SECRET_PATTERNS.sub("[REDACTED]", text)
