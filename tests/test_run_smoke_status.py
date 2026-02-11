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

    checks = run_smoke.collect_status_checks(tmp_path)
    assert all(c.ok for c in checks)
    text, all_ok = run_smoke.format_status_report(checks)
    assert all_ok is True
    assert "GREEN: Project initialized" in text
    assert "READY: all required checks passed." in text


def test_discover_dataset_candidates_and_fetch(tmp_path: Path, monkeypatch) -> None:
    # Simulate official repo origin lookup
    monkeypatch.setattr(run_smoke, "_git_origin", lambda _p: "https://github.com/NOAA-EMC/ufsatm.git")

    ic = tmp_path / "tests" / "data" / "smoke_init.nc"
    lbc = tmp_path / "tests" / "data" / "smoke_lbc.nc"
    ic.parent.mkdir(parents=True)
    ic.write_text("ic\n")
    lbc.write_text("lbc\n")

    candidates = run_smoke.discover_dataset_candidates(tmp_path)
    assert candidates
    selected = candidates[0]
    manifest = run_smoke.fetch_dataset(repo_root=tmp_path, candidate=selected)

    assert manifest.exists()
    text = manifest.read_text(encoding="utf-8")
    assert "source_repo" in text
    assert "ic_file" in text

    checks = run_smoke.collect_status_checks(tmp_path)
    smoke_check = next(c for c in checks if c.name == "Smoke-run sample data")
    assert smoke_check.ok is True
