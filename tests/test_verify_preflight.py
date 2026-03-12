from __future__ import annotations

from pathlib import Path

import typer

from noraa import cli
from noraa.project import ProjectConfig
from noraa.workflow import core_commands


def test_verify_preflight_detects_missing_ccpp_prebuild(tmp_path: Path) -> None:
    result = cli._verify_preflight_failure(
        tmp_path, deps_prefix=None, esmf_mkfile=None, using_verify_script=False, core="mpas"
    )
    assert result is not None
    msg, next_step = result
    assert "Required CCPP submodule content is missing" in msg
    assert "ccpp_prebuild.py" in msg
    assert "git submodule update --init --recursive" in next_step


def test_verify_preflight_ok_when_ccpp_prebuild_exists(tmp_path: Path, monkeypatch) -> None:
    prebuild = tmp_path / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    prebuild.parent.mkdir(parents=True)
    prebuild.write_text("#!/usr/bin/env python3\n")
    deps = tmp_path / ".noraa" / "deps" / "install"
    deps.mkdir(parents=True)
    esmf_mk = tmp_path / ".noraa" / "esmf" / "install" / "lib" / "esmf.mk"
    esmf_mk.parent.mkdir(parents=True)
    esmf_mk.write_text("ESMF\n")
    monkeypatch.setattr(cli, "_cmake_version", lambda: (3, 28, 0))
    monkeypatch.setattr(cli.shutil, "which", lambda *_args, **_kwargs: "/usr/bin/pnetcdf-config")

    assert cli._verify_preflight_failure(
        tmp_path, deps_prefix=None, esmf_mkfile=None, using_verify_script=False, core="mpas"
    ) is None


def test_verify_preflight_detects_missing_deps(tmp_path: Path) -> None:
    prebuild = tmp_path / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    prebuild.parent.mkdir(parents=True)
    prebuild.write_text("#!/usr/bin/env python3\n")
    explicit_esmf = tmp_path / "external" / "esmf.mk"
    explicit_esmf.parent.mkdir(parents=True)
    explicit_esmf.write_text("ESMF\n")

    result = cli._verify_preflight_failure(
        tmp_path,
        deps_prefix=None,
        esmf_mkfile=str(explicit_esmf),
        using_verify_script=False,
        core="mpas",
    )
    assert result is not None
    msg, next_step = result
    assert "UFS dependency bundle not found" in msg
    assert "noraa bootstrap deps --repo" in next_step


def test_verify_preflight_detects_old_cmake(tmp_path: Path, monkeypatch) -> None:
    prebuild = tmp_path / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    prebuild.parent.mkdir(parents=True)
    prebuild.write_text("#!/usr/bin/env python3\n")
    deps = tmp_path / ".noraa" / "deps" / "install"
    deps.mkdir(parents=True)
    explicit_esmf = tmp_path / "external" / "esmf.mk"
    explicit_esmf.parent.mkdir(parents=True)
    explicit_esmf.write_text("ESMF\n")
    monkeypatch.setattr(cli, "_cmake_version", lambda: (3, 22, 1))
    monkeypatch.setattr(cli.shutil, "which", lambda *_args, **_kwargs: "/usr/bin/pnetcdf-config")

    result = cli._verify_preflight_failure(
        tmp_path,
        deps_prefix=None,
        esmf_mkfile=str(explicit_esmf),
        using_verify_script=False,
        core="mpas",
    )
    assert result is not None
    msg, next_step = result
    assert "CMake >= 3.28 is required" in msg
    assert "cmake>=3.28" in next_step


def test_verify_preflight_detects_missing_esmf(tmp_path: Path, monkeypatch) -> None:
    prebuild = tmp_path / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    prebuild.parent.mkdir(parents=True)
    prebuild.write_text("#!/usr/bin/env python3\n")
    deps = tmp_path / ".noraa" / "deps" / "install"
    deps.mkdir(parents=True)
    monkeypatch.setattr(cli, "_cmake_version", lambda: (3, 28, 0))

    result = cli._verify_preflight_failure(
        tmp_path, deps_prefix=None, esmf_mkfile=None, using_verify_script=False, core="mpas"
    )
    assert result is not None
    msg, next_step = result
    assert msg.startswith("Issue identified: ESMF not found")
    assert next_step.startswith("noraa bootstrap esmf --repo")

def test_format_preflight_failure_includes_action_required() -> None:
    text = cli._format_preflight_failure(
        "Issue identified: ESMF not found",
        "noraa bootstrap esmf --repo /tmp/ufsatm",
    )
    assert text.splitlines()[0] == "Issue identified: ESMF not found"
    assert text.splitlines()[1] == "Action required: noraa bootstrap esmf --repo /tmp/ufsatm"

def test_verify_preflight_issues_collects_multiple(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_cmake_version", lambda: (3, 22, 1))

    issues = cli._verify_preflight_issues(
        tmp_path,
        deps_prefix=None,
        esmf_mkfile=None,
        using_verify_script=False,
        core="mpas",
    )
    text = "\n".join(i for i, _ in issues)
    assert "Required CCPP submodule content is missing" in text
    assert "ESMF not found" in text
    assert "UFS dependency bundle not found" in text
    assert "pnetcdf-config not found" in text
    assert "CMake >= 3.28 is required" in text


def test_verify_preflight_fv3_does_not_require_pnetcdf(tmp_path: Path, monkeypatch) -> None:
    prebuild = tmp_path / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    prebuild.parent.mkdir(parents=True)
    prebuild.write_text("#!/usr/bin/env python3\n")
    deps = tmp_path / ".noraa" / "deps" / "install"
    deps.mkdir(parents=True)
    esmf_mk = tmp_path / ".noraa" / "esmf" / "install" / "lib" / "esmf.mk"
    esmf_mk.parent.mkdir(parents=True)
    esmf_mk.write_text("ESMF\n")
    monkeypatch.setattr(cli, "_cmake_version", lambda: (3, 28, 0))
    monkeypatch.setattr(cli.shutil, "which", lambda *_args, **_kwargs: None)

    issues = cli._verify_preflight_issues(
        tmp_path,
        deps_prefix=None,
        esmf_mkfile=None,
        using_verify_script=False,
        core="fv3",
    )
    text = "\n".join(i for i, _ in issues)
    assert "pnetcdf-config not found" not in text


def test_format_preflight_summary_contains_action_lines() -> None:
    summary = cli._format_preflight_summary(
        [
            (
                "Issue identified: ESMF not found",
                "noraa bootstrap esmf --repo /tmp/ufsatm",
            ),
            (
                "Issue identified: CMake too old",
                "pip install -U 'cmake>=3.28'",
            ),
        ]
    )
    assert "Preflight identified blocking issues:" in summary
    assert "Action required: noraa bootstrap esmf --repo /tmp/ufsatm" in summary
    assert "Action required: pip install -U 'cmake>=3.28'" in summary


def test_fv3_preflight_only_does_not_apply_local_fallbacks(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    cfg = ProjectConfig(
        repo_path=str(tmp_path),
        core="fv3",
        verify_script="scripts/verify_fv3_smoke.sh",
    )
    _, compat = core_commands.register_core_commands(
        app=typer.Typer(),
        target_repo=lambda _path: tmp_path,
        require_project=lambda _repo_root: cfg,
        normalize_core=lambda value: value,
    )
    monkeypatch.setattr(
        core_commands.preflight,
        "verify_preflight_issues",
        lambda *_args, **_kwargs: [],
    )

    fallback_calls: list[str] = []

    def _record(name: str):
        def inner(*_args, **_kwargs):
            fallback_calls.append(name)
            return False

        return inner

    monkeypatch.setattr(
        core_commands, "_apply_fv3_fms_r8_fallback", _record("fms_r8")
    )
    monkeypatch.setattr(
        core_commands, "_apply_fv3_fms_required_fallback", _record("fms_required")
    )
    monkeypatch.setattr(
        core_commands,
        "_apply_fv3_top_level_dependency_guards",
        _record("dependency_guards"),
    )
    monkeypatch.setattr(
        core_commands, "_apply_fv3_external_sst_fallback", _record("external_sst")
    )
    monkeypatch.setattr(
        core_commands,
        "_apply_fv3_stochastic_wrapper_stub",
        _record("stochastic_wrapper"),
    )
    monkeypatch.setattr(
        core_commands, "_apply_fv3_update_ca_fallback", _record("update_ca")
    )
    monkeypatch.setattr(
        core_commands,
        "_apply_fv3_stochy_pattern_fallback",
        _record("stochy_pattern"),
    )
    monkeypatch.setattr(
        core_commands, "_apply_fv3_fv_dynamics_kind_fix", _record("fv_dynamics")
    )

    compat["run_verify"](
        repo_root=tmp_path,
        core="fv3",
        deps_prefix=None,
        esmf_mkfile=None,
        clean=True,
        preflight_only=True,
        fv3_fms_r8_fallback=True,
    )

    out = capsys.readouterr().out
    assert "FV3 verify mode: using NORAA CMake fallback path." in out
    assert "Preflight OK. No blocking issues found." in out
    assert "Applied local FV3 fallback" not in out
    assert fallback_calls == []
