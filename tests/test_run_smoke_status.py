from __future__ import annotations

from pathlib import Path

from noraa.workflow import run_smoke


def test_collect_status_checks_not_ready(tmp_path: Path) -> None:
    checks = run_smoke.collect_status_checks(tmp_path)
    assert len(checks) == 6
    assert any(not c.ok for c in checks)
    text, all_ok = run_smoke.format_status_report(checks)
    assert all_ok is False
    assert "RED: Project initialized" in text
    assert "NOT READY:" in text
    assert "Action required:" in text


def test_collect_status_checks_ready(tmp_path: Path) -> None:
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
    exe.write_text("bin\n")

    data = tmp_path / ".noraa" / "runs" / "smoke" / "data"
    data.mkdir(parents=True)
    (data / "sample.nc").write_text("x\n")

    checks = run_smoke.collect_status_checks(tmp_path)
    assert all(c.ok for c in checks)
    text, all_ok = run_smoke.format_status_report(checks)
    assert all_ok is True
    assert "GREEN: Project initialized" in text
    assert "READY: all required checks passed." in text

