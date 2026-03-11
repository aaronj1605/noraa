from __future__ import annotations

from pathlib import Path

from noraa.buildsystem.configure import cmake_fallback_core


def test_cmake_fallback_core_sets_fv3_precision_flags(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    out = tmp_path / "out"
    repo.mkdir()
    out.mkdir()

    calls: list[list[str]] = []

    def fake_run_streamed(cmd: list[str], cwd: Path, out_dir: Path, env: dict[str, str]) -> int:
        calls.append(cmd)
        return 0

    monkeypatch.setattr("noraa.buildsystem.configure.run_streamed", fake_run_streamed)

    rc = cmake_fallback_core(
        repo_root=repo,
        out=out,
        env={},
        clean=False,
        deps_prefix=None,
        esmf_mkfile=None,
        python_executable="/usr/bin/python3",
        core="fv3",
    )

    assert rc == 0
    assert calls
    configure_cmd = calls[0]
    assert "-DFV3=ON" in configure_cmd
    assert "-DMPAS=OFF" in configure_cmd
    assert "-D32BIT=ON" in configure_cmd
    assert "-DCCPP_32BIT=ON" in configure_cmd
    assert "-DRRTMGP_32BIT=ON" in configure_cmd
