from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from noraa.workflow import run_smoke


def test_validate_s3_prefix_rejects_unsafe_values() -> None:
    with pytest.raises(ValueError):
        run_smoke._validate_s3_prefix("")
    with pytest.raises(ValueError):
        run_smoke._validate_s3_prefix("s3://noaa-ufs-htf-pds/path")
    with pytest.raises(ValueError):
        run_smoke._validate_s3_prefix("../bad/path")
    with pytest.raises(ValueError):
        run_smoke._validate_s3_prefix(r"bad\path")


def test_fetch_local_dataset_rejects_path_like_dataset_name(tmp_path: Path) -> None:
    src = tmp_path / "source"
    src.mkdir()
    (src / "a.nc").write_text("x\n", encoding="utf-8")

    with pytest.raises(ValueError):
        run_smoke.fetch_local_dataset(repo_root=tmp_path, local_path=src, dataset_name="../escape")


def test_fetch_local_dataset_replaces_existing_dataset_dir(tmp_path: Path) -> None:
    data_root = tmp_path / ".noraa" / "runs" / "smoke" / "data" / "sample"
    data_root.mkdir(parents=True)
    (data_root / "stale.txt").write_text("old\n", encoding="utf-8")

    src = tmp_path / "source"
    src.mkdir()
    (src / "x1.40962.init.nc").write_text("new\n", encoding="utf-8")

    run_smoke.fetch_local_dataset(repo_root=tmp_path, local_path=src, dataset_name="sample")

    assert not (data_root / "stale.txt").exists()
    assert (data_root / "x1.40962.init.nc").exists()


def test_fetch_local_dataset_auto_fix_injects_reference_time(tmp_path: Path) -> None:
    src = tmp_path / "source"
    src.mkdir()
    (src / "namelist.atmosphere").write_text(
        "&nhyd_model\n config_calendar_type='gregorian'\n config_start_time='2012-01-01_00:00:00'\n/\n",
        encoding="utf-8",
    )
    (src / "streams.atmosphere").write_text(
        '<streams>\n'
        '<immutable_stream name="input" type="input" filename_template="x1.40962.init.nc" input_interval="initial_only"/>\n'
        '<stream name="surface" type="input" filename_template="x1.40962.sfc_update.nc" input_interval="none"/>\n'
        "</streams>\n",
        encoding="utf-8",
    )
    (src / "x1.40962.init.nc").write_text("x\n", encoding="utf-8")

    run_smoke.fetch_local_dataset(
        repo_root=tmp_path,
        local_path=src,
        dataset_name="sample",
        auto_fix_mpas_compat=True,
    )

    imported = tmp_path / ".noraa" / "runs" / "smoke" / "data" / "sample"
    streams_text = (imported / "streams.atmosphere").read_text(encoding="utf-8")
    assert 'reference_time="2012-01-01_00:00:00"' in streams_text
    assert (imported / "x1.40962.sfc_update.nc").exists()

    manifest = (tmp_path / ".noraa" / "runs" / "smoke" / "data" / "dataset.toml").read_text(
        encoding="utf-8"
    )
    assert "compat_fixes = [" in manifest


def test_safe_extract_tar_gz_blocks_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("x\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(payload, arcname="../escape.txt")

    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ValueError):
        run_smoke._safe_extract_tar_gz(archive, dest)


def test_safe_extract_tar_gz_blocks_too_many_members(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "many.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("x\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(payload, arcname="payload.txt")

    dest = tmp_path / "out"
    dest.mkdir()
    monkeypatch.setattr(run_smoke, "MAX_ARCHIVE_MEMBERS", 0)
    with pytest.raises(ValueError):
        run_smoke._safe_extract_tar_gz(archive, dest)


def test_safe_extract_tar_gz_blocks_too_large_payload(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "large.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("too-big\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(payload, arcname="payload.txt")

    dest = tmp_path / "out"
    dest.mkdir()
    monkeypatch.setattr(run_smoke, "MAX_ARCHIVE_TOTAL_BYTES", 1)
    with pytest.raises(ValueError):
        run_smoke._safe_extract_tar_gz(archive, dest)


def test_fetch_official_bundle_writes_archive_sha256(tmp_path: Path, monkeypatch) -> None:
    source_tar = tmp_path / "source.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("x\n", encoding="utf-8")
    with tarfile.open(source_tar, "w:gz") as tf:
        tf.add(payload, arcname="case/payload.txt")

    def _fake_urlretrieve(_url: str, dest: Path):
        Path(dest).write_bytes(source_tar.read_bytes())
        return (str(dest), None)

    monkeypatch.setattr(run_smoke.urllib.request, "urlretrieve", _fake_urlretrieve)
    dataset = run_smoke.OfficialDataset(
        dataset_id="supercell",
        url="https://example.test/supercell.tar.gz",
        source_repo="https://example.test/repo",
        description="test bundle",
        runtime_compatible=False,
        runtime_note="metadata-only",
    )

    manifest = run_smoke.fetch_official_bundle(repo_root=tmp_path, dataset=dataset)
    text = manifest.read_text(encoding="utf-8")
    assert 'archive_sha256 = "' in text


def test_fetch_official_bundle_rejects_hash_mismatch(tmp_path: Path, monkeypatch) -> None:
    source_tar = tmp_path / "source.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("x\n", encoding="utf-8")
    with tarfile.open(source_tar, "w:gz") as tf:
        tf.add(payload, arcname="case/payload.txt")

    def _fake_urlretrieve(_url: str, dest: Path):
        Path(dest).write_bytes(source_tar.read_bytes())
        return (str(dest), None)

    monkeypatch.setattr(run_smoke.urllib.request, "urlretrieve", _fake_urlretrieve)
    dataset = run_smoke.OfficialDataset(
        dataset_id="supercell",
        url="https://example.test/supercell.tar.gz",
        source_repo="https://example.test/repo",
        description="test bundle",
        runtime_compatible=False,
        runtime_note="metadata-only",
        sha256="deadbeef",
    )

    with pytest.raises(ValueError):
        run_smoke.fetch_official_bundle(repo_root=tmp_path, dataset=dataset)


def test_git_origin_returns_empty_on_git_failure(tmp_path: Path, monkeypatch) -> None:
    def _raise(*_args, **_kwargs):
        raise RuntimeError("git failed")

    monkeypatch.setattr(run_smoke, "safe_check_output", _raise)
    assert run_smoke._git_origin(tmp_path) == ""
