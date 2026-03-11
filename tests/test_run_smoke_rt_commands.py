from __future__ import annotations

from pathlib import Path

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
