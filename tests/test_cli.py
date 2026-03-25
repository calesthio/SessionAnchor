"""Tests for the CLI entry point."""

import os
import subprocess
import sys
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def _cli_env():
    pythonpath = os.pathsep.join(
        [str(SRC_DIR), os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [str(SRC_DIR)]
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = pythonpath
    return env


def test_help():
    result = subprocess.run(
        [sys.executable, "-m", "sessionanchor", "--help"],
        capture_output=True, text=True,
        env=_cli_env(),
    )
    assert result.returncode == 0
    assert "SessionAnchor" in result.stdout


def test_version():
    result = subprocess.run(
        [sys.executable, "-m", "sessionanchor", "version"],
        capture_output=True, text=True,
        env=_cli_env(),
    )
    assert result.returncode == 0
    assert "0.1.0" in result.stdout


def test_unknown_command():
    result = subprocess.run(
        [sys.executable, "-m", "sessionanchor", "nonexistent"],
        capture_output=True, text=True,
        env=_cli_env(),
    )
    assert result.returncode == 1
