"""Tests for .contextignore parser."""

from sessionanchor.ignore import should_ignore, redact_secrets, load_patterns, DEFAULT_PATTERNS


def test_directory_pattern():
    patterns = ["node_modules/", "dist/"]
    assert should_ignore("node_modules/foo.js", patterns)
    assert should_ignore("src/node_modules/bar.js", patterns)
    assert should_ignore("dist/bundle.js", patterns)
    assert not should_ignore("src/app.js", patterns)


def test_file_pattern():
    patterns = [".env", ".env.*", "*.key"]
    assert should_ignore(".env", patterns)
    assert should_ignore(".env.local", patterns)
    assert should_ignore("server.key", patterns)
    assert not should_ignore("app.py", patterns)


def test_glob_pattern():
    patterns = ["*.pyc", "*.pem"]
    assert should_ignore("module.pyc", patterns)
    assert should_ignore("cert.pem", patterns)
    assert not should_ignore("module.py", patterns)


def test_redact_aws_key():
    text = "aws_key = AKIAIOSFODNN7EXAMPLE"
    result = redact_secrets(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in result
    assert "[REDACTED]" in result


def test_redact_openai_key():
    text = "key = sk-abcdefghijklmnopqrstuvwxyz1234"
    result = redact_secrets(text)
    assert "sk-abcdef" not in result
    assert "[REDACTED]" in result


def test_redact_github_token():
    text = "token = ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
    result = redact_secrets(text)
    assert "ghp_" not in result
    assert "[REDACTED]" in result


def test_redact_jwt():
    text = "bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc"
    result = redact_secrets(text)
    assert "eyJhbGci" not in result


def test_no_redaction_needed():
    text = "This is normal text with no secrets"
    assert redact_secrets(text) == text


def test_default_patterns_has_essentials():
    lines = [l.strip() for l in DEFAULT_PATTERNS.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert ".env" in lines
    assert "node_modules/" in lines
    assert ".claude/memory/" in lines
