"""Tests for the CLI entry point."""

import subprocess
import sys


def test_help():
    result = subprocess.run(
        [sys.executable, "-m", "claude_context", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "claude-context" in result.stdout


def test_version():
    result = subprocess.run(
        [sys.executable, "-m", "claude_context", "version"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "0.1.0" in result.stdout


def test_unknown_command():
    result = subprocess.run(
        [sys.executable, "-m", "claude_context", "nonexistent"],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
