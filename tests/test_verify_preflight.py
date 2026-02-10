from __future__ import annotations

from pathlib import Path

from noraa import cli


def test_verify_preflight_detects_missing_ccpp_prebuild(tmp_path: Path) -> None:
    result = cli._verify_preflight_failure(
        tmp_path, deps_prefix=None, esmf_mkfile=None, using_verify_script=False
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

    assert cli._verify_preflight_failure(
        tmp_path, deps_prefix=None, esmf_mkfile=None, using_verify_script=False
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
    )
    assert result is not None
    msg, next_step = result
    assert "MPAS dependency bundle not found" in msg
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

    result = cli._verify_preflight_failure(
        tmp_path,
        deps_prefix=None,
        esmf_mkfile=str(explicit_esmf),
        using_verify_script=False,
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
        tmp_path, deps_prefix=None, esmf_mkfile=None, using_verify_script=False
    )
    assert result is not None
    msg, next_step = result
    assert msg.startswith("Issue identified: ESMF not found")
    assert next_step.startswith("noraa bootstrap esmf --repo")