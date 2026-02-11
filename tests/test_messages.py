from __future__ import annotations

from pathlib import Path

import pytest

from noraa.messages import fail, repo_cmd


def test_repo_cmd_formats_with_repo_flag() -> None:
    repo = Path("/tmp/ufsatm")
    cmd = repo_cmd(repo, "bootstrap", "deps")
    assert cmd == "noraa bootstrap deps --repo /tmp/ufsatm"


def test_fail_includes_logs_and_next_step() -> None:
    with pytest.raises(SystemExit) as excinfo:
        fail(
            "Something failed",
            logs=Path("/tmp/logs/run"),
            next_step="noraa diagnose --repo /tmp/ufsatm",
        )

    text = str(excinfo.value)
    assert "Something failed" in text
    assert "Logs: /tmp/logs/run" in text
    assert "Run this command next: noraa diagnose --repo /tmp/ufsatm" in text
