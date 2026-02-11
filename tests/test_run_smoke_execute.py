from __future__ import annotations

import sys
from pathlib import Path

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
