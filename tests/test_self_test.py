from __future__ import annotations

from pathlib import Path

import pytest

from noraa import cli


def test_ensure_pytest_available_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda _name: None)
    with pytest.raises(SystemExit) as excinfo:
        cli._ensure_pytest_available()
    assert "pytest is required for noraa self-test" in str(excinfo.value)


def test_self_test_runs_pytest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "_target_repo", lambda _repo: tmp_path)
    monkeypatch.setattr(cli, "_ensure_pytest_available", lambda: None)

    class _Result:
        returncode = 0

    seen: dict[str, object] = {}

    def _fake_run(cmd, cwd, text, check):
        seen["cmd"] = cmd
        seen["cwd"] = cwd
        seen["text"] = text
        seen["check"] = check
        return _Result()

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)
    cli.self_test(repo=".", pytest_args="-q")
    assert seen["cmd"] == [cli.sys.executable, "-m", "pytest", "-q"]
    assert seen["cwd"] == str(tmp_path)
