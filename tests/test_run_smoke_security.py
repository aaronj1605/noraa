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
