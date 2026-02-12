from __future__ import annotations

from pathlib import Path

from noraa.workflow import run_smoke, run_smoke_cli


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


def test_runtime_compatibility_blocks_standalone_supercell_layout(tmp_path: Path) -> None:
    case_dir = tmp_path / ".noraa" / "runs" / "smoke" / "data" / "supercell_local"
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "supercell.ncl").write_text("x\n")
    (case_dir / "supercell.graph.info").write_text("x\n")
    (case_dir / "namelist.init_atmosphere").write_text("&x\n/\n")
    (case_dir / "streams.init_atmosphere").write_text("<streams/>\n")
    (tmp_path / ".noraa" / "runs" / "smoke" / "data" / "dataset.toml").write_text(
        "[dataset]\n"
        'name = "supercell_local"\n'
        'source_repo = "user-local"\n'
        'source_path = "/tmp/supercell"\n'
        'bundle_dir = "supercell_local"\n'
        "runtime_compatible = true\n"
    )
    ok, detail, action = run_smoke.smoke_runtime_compatibility(tmp_path)
    assert ok is False
    assert "standalone" in detail.lower()
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


def test_fetch_official_prints_compatibility_label(tmp_path: Path, monkeypatch, capsys) -> None:
    dataset = run_smoke.OfficialDataset(
        dataset_id="supercell",
        url="https://example.test/supercell.tar.gz",
        source_repo="https://mpas-dev.github.io/atmosphere/test_cases.html",
        description="MPAS idealized supercell test-case bundle",
        runtime_compatible=False,
        runtime_note="metadata-only",
    )
    dataset2 = run_smoke.OfficialDataset(
        dataset_id="mountain_wave",
        url="https://example.test/mountain_wave.tar.gz",
        source_repo="https://mpas-dev.github.io/atmosphere/test_cases.html",
        description="MPAS idealized mountain-wave test-case bundle",
        runtime_compatible=False,
        runtime_note="metadata-only",
    )

    monkeypatch.setattr(run_smoke, "official_catalog", lambda: [dataset, dataset2])
    monkeypatch.setattr(run_smoke, "resolve_official_dataset", lambda _dataset_id: dataset)
    monkeypatch.setattr(
        run_smoke,
        "fetch_official_bundle",
        lambda *, repo_root, dataset: repo_root / ".noraa" / "runs" / "smoke" / "data" / "dataset.toml",
    )

    run_smoke_cli.fetch_official(
        repo_root=tmp_path,
        dataset=None,
        yes=False,
        prompt_int=lambda _msg: 1,
    )

    output = capsys.readouterr().out
    assert "[compat: metadata-only]" in output


def test_fetch_official_regtests_prefix_writes_manifest(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd, check, text):
        calls.append(cmd)
        return None

    monkeypatch.setattr(run_smoke.subprocess, "run", _fake_run)
    manifest = run_smoke.fetch_official_regtests_prefix(
        repo_root=tmp_path,
        s3_prefix="input-data-20251015/MPAS",
        aws_bin="aws",
    )

    assert calls
    assert calls[0][:6] == [
        "aws",
        "s3",
        "cp",
        "--recursive",
        "--no-sign-request",
        "s3://noaa-ufs-regtests-pds/input-data-20251015/MPAS/",
    ]
    text = manifest.read_text(encoding="utf-8")
    assert 'source_repo = "https://noaa-ufs-regtests-pds.s3.amazonaws.com"' in text
    assert 'runtime_note = "Fetched from noaa-ufs-regtests-pds. NORAA will validate required runtime files."' in text


def test_validate_runtime_case_dir_checks_streams_references(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir(parents=True)
    (case_dir / "namelist.atmosphere").write_text(
        "&nhyd_model\n config_calendar_type='gregorian'\n/\n",
        encoding="utf-8",
    )
    (case_dir / "streams.atmosphere").write_text(
        '<stream name="input" filename_template="missing_input.nc"/>\n',
        encoding="utf-8",
    )

    ok, detail = run_smoke.validate_runtime_case_dir(case_dir)
    assert ok is False
    assert "streams.atmosphere references missing runtime files" in detail
    assert "missing_input.nc" in detail


def test_format_status_short_not_ready_lists_failed_checks() -> None:
    checks = [
        run_smoke.StatusCheck(name="A", ok=True, detail="a"),
        run_smoke.StatusCheck(name="B", ok=False, detail="b"),
        run_smoke.StatusCheck(name="C", ok=False, detail="c"),
    ]
    text, ready = run_smoke.format_status_short(checks)
    assert ready is False
    assert "NOT READY:" in text
    assert "B" in text
    assert "C" in text


def test_fetch_official_regtests_dry_run_does_not_download(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(run_smoke_cli.shutil, "which", lambda _x: "aws")
    called = {"fetch": False}

    def _unexpected_fetch(**_kwargs):
        called["fetch"] = True
        raise AssertionError("fetch_official_regtests_prefix should not run in dry-run mode")

    monkeypatch.setattr(run_smoke, "fetch_official_regtests_prefix", _unexpected_fetch)
    run_smoke_cli.fetch_official_regtests(
        repo_root=tmp_path,
        s3_prefix="input-data-20251015/MPAS",
        dry_run=True,
    )

    output = capsys.readouterr().out
    assert "Dry run: official-regtests fetch preview" in output
    assert called["fetch"] is False


def test_fetch_local_dry_run_reports_metadata_only(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "x1.40962.init.nc").write_text("x\n", encoding="utf-8")
    run_smoke_cli.fetch_local_dry_run(local_path=str(data_dir))
    output = capsys.readouterr().out
    assert "metadata-only" in output


def test_clean_data_removes_dataset_and_manifest(tmp_path: Path) -> None:
    data_root = tmp_path / ".noraa" / "runs" / "smoke" / "data"
    case = data_root / "sample"
    case.mkdir(parents=True)
    (case / "x.txt").write_text("x\n", encoding="utf-8")
    manifest = data_root / "dataset.toml"
    manifest.write_text(
        "[dataset]\n"
        'name = "sample"\n',
        encoding="utf-8",
    )

    run_smoke_cli.clean_data(repo_root=tmp_path, dataset="sample")
    assert not case.exists()
    assert not manifest.exists()
