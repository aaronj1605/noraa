from __future__ import annotations

from pathlib import Path

import pytest

from noraa import cli


def test_confirm_or_fail_accepts_with_assume_yes() -> None:
    cli._confirm_or_fail(
        prompt="ignored",
        assume_yes=True,
        failure_message="should not fail",
        next_step="noraa init",
    )


def test_confirm_or_fail_raises_when_user_declines(monkeypatch) -> None:
    monkeypatch.setattr(cli.typer, "confirm", lambda *_args, **_kwargs: False)
    with pytest.raises(SystemExit) as excinfo:
        cli._confirm_or_fail(
            prompt="decline",
            assume_yes=False,
            failure_message="blocked",
            next_step="noraa init",
        )
    text = str(excinfo.value)
    assert "blocked" in text
    assert "Next step: noraa init" in text


def test_build_mpas_calls_init_and_verify_with_explicit_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, dict] = {}

    prebuild = tmp_path / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    prebuild.parent.mkdir(parents=True)
    prebuild.write_text("#!/usr/bin/env python3\n")

    monkeypatch.setattr(cli, "_target_repo", lambda _repo: tmp_path)
    monkeypatch.setattr(cli, "_require_project", lambda _repo: object())
    monkeypatch.setattr(cli, "_confirm_or_fail", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "load_project", lambda _repo: None)
    monkeypatch.setattr(
        cli.guided_build, "bootstrapped_esmf_mk", lambda _repo: tmp_path / "esmf.mk"
    )
    monkeypatch.setattr(
        cli.guided_build,
        "bootstrapped_deps_prefix",
        lambda _repo: tmp_path / ".noraa" / "deps" / "install",
    )

    def fake_init(**kwargs):
        calls["init"] = kwargs

    def fake_verify(**kwargs):
        calls["verify"] = kwargs

    monkeypatch.setattr(cli, "init", fake_init)
    monkeypatch.setattr(cli, "verify", fake_verify)

    cli.build_mpas(repo=".", clean=True, yes=True, esmf_branch="v8.6.1")

    assert calls["init"]["repo"] == str(tmp_path)
    assert calls["init"]["force"] is False
    assert calls["init"]["upstream_url"] == "https://github.com/NOAA-EMC/ufsatm.git"
    assert calls["verify"]["repo"] == str(tmp_path)
    assert calls["verify"]["deps_prefix"] is None
    assert calls["verify"]["esmf_mkfile"] is None
    assert calls["verify"]["clean"] is True
    assert calls["verify"]["preflight_only"] is False
