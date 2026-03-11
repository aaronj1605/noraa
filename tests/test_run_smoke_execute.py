from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from noraa.workflow import run_smoke


def _make_ready_repo(tmp_path: Path) -> None:
    (tmp_path / ".noraa" / "project.toml").parent.mkdir(parents=True)
    (tmp_path / ".noraa" / "project.toml").write_text("[project]\n")

    prebuild = tmp_path / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    prebuild.parent.mkdir(parents=True)
    prebuild.write_text("#!/usr/bin/env python3\n")

    deps = tmp_path / ".noraa" / "deps" / "install"
    deps.mkdir(parents=True)

    mk = tmp_path / ".noraa" / "esmf" / "install" / "lib" / "esmf.mk"
    mk.parent.mkdir(parents=True)
    mk.write_text("ESMF\n")

    exe = tmp_path / ".noraa" / "build" / "bin" / "mpas_atmosphere"
    exe.parent.mkdir(parents=True)
    exe.write_text("placeholder\n")

    data = tmp_path / ".noraa" / "runs" / "smoke" / "data" / "sample_case"
    data.mkdir(parents=True)
    (data / "sample_init.nc").write_text("x\n")
    manifest = tmp_path / ".noraa" / "runs" / "smoke" / "data" / "dataset.toml"
    manifest.write_text(
        "[dataset]\n"
        'name = "sample_case"\n'
        'source_repo = "https://github.com/NOAA-EMC/ufsatm.git"\n'
        'source_path = "/tmp/sample_init.nc"\n'
        'ic_file = "sample_case/sample_init.nc"\n'
    )


def test_execute_smoke_accepts_runtime_reachable_nonzero(tmp_path: Path) -> None:
    _make_ready_repo(tmp_path)
    script = tmp_path / "emit_runtime_hint.py"
    script.write_text("import sys\nprint('namelist missing')\nsys.exit(2)\n", encoding="utf-8")

    result = run_smoke.execute_smoke(
        repo_root=tmp_path,
        timeout_sec=5,
        command_text=f"{sys.executable} {script}",
    )

    assert result.ok is True
    assert result.returncode == 2
    assert (result.run_dir / "command.txt").exists()
    assert (result.run_dir / "stdout.txt").exists()
    assert (result.run_dir / "stderr.txt").exists()
    assert (result.run_dir / "result.txt").exists()
    assert run_smoke.execution_label(result) == "REACHED_RUNTIME_NONZERO"


def test_execute_smoke_fails_on_unrecognized_nonzero(tmp_path: Path) -> None:
    _make_ready_repo(tmp_path)
    script = tmp_path / "emit_unknown_error.py"
    script.write_text("import sys\nprint('fatal unknown error')\nsys.exit(3)\n", encoding="utf-8")

    result = run_smoke.execute_smoke(
        repo_root=tmp_path,
        timeout_sec=5,
        command_text=f"{sys.executable} {script}",
    )

    assert result.ok is False
    assert result.returncode == 3
    assert "failed with return code 3" in result.reason
    assert run_smoke.execution_label(result) == "FAIL"


def test_execute_label_pass() -> None:
    result = run_smoke.ExecuteResult(
        ok=True,
        run_dir=Path("."),
        command=["x"],
        returncode=0,
        reason="ok",
    )
    assert run_smoke.execution_label(result) == "PASS"


def test_execute_label_runtime_timeout() -> None:
    result = run_smoke.ExecuteResult(
        ok=True,
        run_dir=Path("."),
        command=["x"],
        returncode=None,
        reason="timeout",
    )
    assert run_smoke.execution_label(result) == "REACHED_RUNTIME_TIMEOUT"


def test_execute_smoke_accepts_runtime_reachable_from_mpas_log(
    tmp_path: Path, monkeypatch
) -> None:
    _make_ready_repo(tmp_path)

    def _fake_run(*_args, **_kwargs):
        run_dir = Path(_kwargs["cwd"])
        (run_dir / "log.atmosphere.0000.out").write_text(
            "Reading namelist from file namelist.atmosphere\n"
            "Reading streams configuration from file streams.atmosphere\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=76, stdout="", stderr="")

    monkeypatch.setattr(run_smoke.subprocess, "run", _fake_run)

    result = run_smoke.execute_smoke(
        repo_root=tmp_path,
        timeout_sec=5,
        command_text=f"{sys.executable} -c \"print('x')\"",
    )

    assert result.ok is True
    assert result.returncode == 76
    assert run_smoke.execution_label(result) == "REACHED_RUNTIME_NONZERO"


def test_execute_smoke_requires_output_artifacts_when_enabled(tmp_path: Path) -> None:
    _make_ready_repo(tmp_path)
    script = tmp_path / "ok_no_output.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    result = run_smoke.execute_smoke(
        repo_root=tmp_path,
        timeout_sec=5,
        command_text=f"{sys.executable} {script}",
        require_artifacts=True,
    )

    assert result.ok is False
    assert result.returncode == 0
    assert "no output artifacts" in result.reason.lower()


def test_execute_smoke_runtime_fatal_marker_blocks_success(tmp_path: Path, monkeypatch) -> None:
    _make_ready_repo(tmp_path)

    def _fake_run(*_args, **_kwargs):
        run_dir = Path(_kwargs["cwd"])
        (run_dir / "log.atmosphere.0000.out").write_text(
            "Reading namelist from file namelist.atmosphere\n"
            "CRITICAL ERROR: xml stream parser failed: streams.atmosphere\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=76, stdout="", stderr="")

    monkeypatch.setattr(run_smoke.subprocess, "run", _fake_run)

    result = run_smoke.execute_smoke(
        repo_root=tmp_path,
        timeout_sec=5,
        command_text=f"{sys.executable} -c \"print('x')\"",
    )

    assert result.ok is False
    assert result.returncode == 76
    assert "xml stream parser failed" in result.reason.lower()


def test_execute_smoke_stages_case_files_for_default_command(tmp_path: Path, monkeypatch) -> None:
    _make_ready_repo(tmp_path)
    case_dir = tmp_path / ".noraa" / "runs" / "smoke" / "data" / "sample_case"
    (case_dir / "namelist.atmosphere").write_text("&nhyd_model\n/\n", encoding="utf-8")
    (case_dir / "streams.atmosphere").write_text("<streams/>\n", encoding="utf-8")
    manifest = tmp_path / ".noraa" / "runs" / "smoke" / "data" / "dataset.toml"
    manifest.write_text(
        "[dataset]\n"
        'name = "sample_case"\n'
        'source_repo = "user-local"\n'
        'source_path = "/tmp/sample_case"\n'
        'bundle_dir = "sample_case"\n'
        "runtime_compatible = true\n",
        encoding="utf-8",
    )

    def _fake_run(*_args, **_kwargs):
        run_dir = Path(_kwargs["cwd"])
        assert (run_dir / "namelist.atmosphere").exists()
        assert (run_dir / "streams.atmosphere").exists()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(run_smoke.subprocess, "run", _fake_run)

    result = run_smoke.execute_smoke(repo_root=tmp_path, timeout_sec=5, command_text=None)
    assert result.ok is True


def test_execute_smoke_uses_fv3_default_executable_when_core_fv3(
    tmp_path: Path, monkeypatch
) -> None:
    _make_ready_repo(tmp_path)
    (tmp_path / ".noraa" / "project.toml").write_text(
        "[project]\nrepo_path = \"/tmp/x\"\n\n"
        "[git]\nupstream_url = \"https://github.com/NOAA-EMC/ufsatm.git\"\nallow_fork = false\nfork_url = \"\"\n\n"
        "[build]\ncore = \"fv3\"\nverify_script = \"scripts/verify_fv3_smoke.sh\"\n",
        encoding="utf-8",
    )
    mpas = tmp_path / ".noraa" / "build" / "bin" / "mpas_atmosphere"
    if mpas.exists():
        mpas.unlink()
    fv3 = tmp_path / ".noraa" / "build" / "bin" / "ufs_model"
    fv3.parent.mkdir(parents=True, exist_ok=True)
    fv3.write_text("placeholder\n", encoding="utf-8")

    seen = {"cmd": None}

    def _fake_run(cmd, **_kwargs):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(run_smoke.subprocess, "run", _fake_run)
    result = run_smoke.execute_smoke(repo_root=tmp_path, timeout_sec=5, command_text=None)
    assert result.ok is True
    assert seen["cmd"] == [str(fv3)]
