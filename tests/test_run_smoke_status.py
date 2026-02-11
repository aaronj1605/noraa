from __future__ import annotations

from pathlib import Path

from noraa.workflow import run_smoke


def test_collect_status_checks_not_ready(tmp_path: Path) -> None:
    checks = run_smoke.collect_status_checks(tmp_path)
    assert len(checks) == 7
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

    data = tmp_path / ".noraa" / "runs" / "smoke" / "data" / "sample_case" / "sample_case"
    data.mkdir(parents=True)
    (data / "sample_init.nc").write_text("x\n")
    (data / "streams.atmosphere").write_text("&streams\n/\n")
    (data / "namelist.atmosphere").write_text("&nhyd_model\n config_calendar_type='gregorian'\n/\n")
    manifest = tmp_path / ".noraa" / "runs" / "smoke" / "data" / "dataset.toml"
    manifest.write_text(
        "[dataset]\n"
        'name = "sample_case"\n'
        'source_repo = "https://github.com/NOAA-EMC/ufsatm.git"\n'
        'source_path = "/tmp/sample_init.nc"\n'
        'bundle_dir = "sample_case"\n'
        "runtime_compatible = true\n"
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


def test_discovery_excludes_noraa_esmf_noise(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(run_smoke, "_git_origin", lambda _p: "https://github.com/NOAA-EMC/ufsatm.git")

    # This should be ignored (false positive seen in real usage).
    noise = tmp_path / ".noraa" / "esmf" / "src" / "src" / "Infrastructure" / "Field" / "tests" / "data" / "C48_mosaic.nc"
    noise.parent.mkdir(parents=True)
    noise.write_text("x\n")

    # This should be accepted.
    ic = tmp_path / "tests" / "data" / "sample_init.nc"
    ic.parent.mkdir(parents=True)
    ic.write_text("ic\n")

    candidates = run_smoke.discover_dataset_candidates(tmp_path)
    paths = [str(c.ic_file) for c in candidates]
    assert str(ic) in paths
    assert str(noise) not in paths


def test_runtime_compatibility_reports_metadata_only_manifest(tmp_path: Path) -> None:
    data_root = tmp_path / ".noraa" / "runs" / "smoke" / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "dataset.toml").write_text(
        "[dataset]\n"
        'name = "supercell"\n'
        'source_repo = "https://mpas-dev.github.io/atmosphere/test_cases.html"\n'
        'source_path = "https://example/supercell.tar.gz"\n'
        'bundle_dir = "supercell"\n'
        "runtime_compatible = false\n"
        'runtime_note = "metadata-only"\n'
    )
    ok, detail, action = run_smoke.smoke_runtime_compatibility(tmp_path)
    assert ok is False
    assert "metadata-only" in detail
    assert "fetch-data local" in action


def test_fetch_official_ufs_prefix_writes_manifest_with_citation(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd, check, text):
        calls.append(cmd)
        return None

    monkeypatch.setattr(run_smoke.subprocess, "run", _fake_run)
    manifest = run_smoke.fetch_official_ufs_prefix(
        repo_root=tmp_path,
        s3_prefix="develop-20250530/example_case",
        aws_bin="aws",
        accessed_on="2026-02-11",
    )

    assert calls
    assert calls[0][:6] == ["aws", "s3", "cp", "--recursive", "--no-sign-request", "s3://noaa-ufs-htf-pds/develop-20250530/example_case/"]
    text = manifest.read_text(encoding="utf-8")
    assert 'source_repo = "https://registry.opendata.aws/noaa-ufs-htf-pds"' in text
    assert 'citation_accessed_on = "2026-02-11"' in text
    assert "NOAA Unified Forecast System (UFS) Hierarchical Testing Framework (HTF)" in text
