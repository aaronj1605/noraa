from __future__ import annotations

from pathlib import Path

import typer
from typer.testing import CliRunner

from noraa.project import ProjectConfig, load_project
from noraa.workflow.run_smoke_rt_commands import (
    _dataset_bundle_dir,
    _is_back_token,
    _looks_like_menu_token,
    _parse_menu_choice,
    _write_fv3_launcher_script,
)


def test_write_fv3_launcher_script_creates_executable(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    exe_path = tmp_path / "ufs_model"
    exe_path.write_text("binary", encoding="utf-8")

    launcher = _write_fv3_launcher_script(
        repo_root=repo_root,
        case_dir=case_dir,
        exe_path=exe_path,
    )

    assert launcher.exists()
    text = launcher.read_text(encoding="utf-8")
    assert "CASE_DIR=" in text
    assert "EXE=" in text
    assert 'cp -a "$CASE_DIR"/. .' in text
    assert 'exec "$EXE"' in text


def test_write_fv3_launcher_script_quotes_paths(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    case_dir = tmp_path / "case with space"
    case_dir.mkdir()
    exe_path = tmp_path / "ufs model"
    exe_path.write_text("binary", encoding="utf-8")

    launcher = _write_fv3_launcher_script(
        repo_root=repo_root,
        case_dir=case_dir,
        exe_path=exe_path,
    )
    text = launcher.read_text(encoding="utf-8")
    assert "CASE_DIR='/" in text or "CASE_DIR=" in text
    assert "EXE='/" in text or "EXE=" in text


def test_dataset_bundle_dir_reads_dataset_manifest(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    data_root = repo_root / ".noraa" / "runs" / "smoke" / "data"
    bundle = data_root / "sample_case"
    bundle.mkdir(parents=True)
    manifest = data_root / "dataset.toml"
    manifest.write_text(
        "[dataset]\n"
        'name = "sample_case"\n'
        'bundle_dir = "sample_case"\n',
        encoding="utf-8",
    )

    resolved = _dataset_bundle_dir(repo_root)
    assert resolved == bundle


def test_dataset_bundle_dir_handles_invalid_toml(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    data_root = repo_root / ".noraa" / "runs" / "smoke" / "data"
    data_root.mkdir(parents=True)
    (data_root / "dataset.toml").write_text("[dataset\nbroken", encoding="utf-8")
    assert _dataset_bundle_dir(repo_root) is None


def test_is_back_token_recognizes_supported_forms() -> None:
    assert _is_back_token("back")
    assert _is_back_token("go back")
    assert _is_back_token("B")
    assert not _is_back_token("2")


def test_parse_menu_choice_handles_valid_invalid_and_back() -> None:
    assert _parse_menu_choice("1", {1, 2}) == 1
    assert _parse_menu_choice("2", {1, 2}) == 2
    assert _parse_menu_choice("3", {1, 2}) is None
    assert _parse_menu_choice("abc", {1, 2}) is None
    assert _parse_menu_choice("back", {1, 2}) == -1


def test_looks_like_menu_token() -> None:
    assert _looks_like_menu_token("1")
    assert _looks_like_menu_token("2")
    assert not _looks_like_menu_token("/tmp/case")


def test_rt_advanced_guide_updates_project_core_before_status(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    app = typer.Typer()
    run_smoke_app = typer.Typer()
    run_smoke_fetch_app = typer.Typer()
    rt_app = typer.Typer()
    app.add_typer(run_smoke_app, name="run-smoke")
    run_smoke_app.add_typer(run_smoke_fetch_app, name="fetch-data")
    app.add_typer(rt_app, name="rt")

    cfg = ProjectConfig(
        repo_path=str(repo_root),
        core="fv3",
        verify_script="scripts/verify_fv3_smoke.sh",
    )

    def require_project(path: Path) -> ProjectConfig:
        assert path == repo_root
        return cfg

    def fetch_official_regtests(**kwargs) -> None:
        data_root = repo_root / ".noraa" / "runs" / "smoke" / "data"
        bundle = data_root / "sample_case"
        bundle.mkdir(parents=True, exist_ok=True)
        (data_root / "dataset.toml").write_text(
            "[dataset]\n"
            'name = "sample_case"\n'
            'bundle_dir = "sample_case"\n',
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "noraa.workflow.run_smoke_rt_commands.run_smoke_cli.fetch_official_regtests",
        fetch_official_regtests,
    )
    monkeypatch.setattr(
        "noraa.workflow.run_smoke_rt_commands.run_smoke_cli.status",
        lambda repo_root: None,
    )
    monkeypatch.setattr(
        "noraa.workflow.run_smoke_rt_commands.run_smoke_cli.execute",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "noraa.workflow.run_smoke_rt_commands.run_smoke.collect_status_checks",
        lambda repo_root: [],
    )
    monkeypatch.setattr(
        "noraa.workflow.run_smoke_rt_commands.run_smoke.format_status_report",
        lambda checks: ("READY", True),
    )
    monkeypatch.setattr(
        "noraa.workflow.run_smoke_rt_commands.run_smoke.validate_runtime_case_dir",
        lambda case_dir: (True, str(case_dir)),
    )

    from noraa.workflow.run_smoke_rt_commands import register_run_smoke_and_rt_commands

    register_run_smoke_and_rt_commands(
        run_smoke_app=run_smoke_app,
        run_smoke_fetch_app=run_smoke_fetch_app,
        rt_app=rt_app,
        target_repo=lambda repo: Path(repo),
        require_project=require_project,
        normalize_core=lambda core: core,
        verify_runner=lambda repo_root, core: None,
    )

    result = CliRunner().invoke(
        app,
        ["rt", "advanced-guide", "--repo", str(repo_root), "--core", "mpas", "--yes"],
    )

    assert result.exit_code == 0, result.stdout
    saved = load_project(repo_root)
    assert saved is not None
    assert saved.core == "mpas"
    assert saved.verify_script == "scripts/verify_mpas_smoke.sh"
    assert "Updated project core default to: mpas" in result.stdout
