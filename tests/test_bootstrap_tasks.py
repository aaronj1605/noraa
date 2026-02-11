from __future__ import annotations

from pathlib import Path

from noraa.bootstrap import tasks


def test_bootstrap_esmf_prints_next_steps(tmp_path: Path, monkeypatch, capsys) -> None:
    repo_root = tmp_path
    src = repo_root / ".noraa" / "esmf" / "src"
    src.mkdir(parents=True)

    out_dir = repo_root / ".noraa" / "logs" / "fake-bootstrap-esmf"
    out_dir.mkdir(parents=True)

    monkeypatch.setattr(tasks, "log_dir", lambda *_args, **_kwargs: out_dir)
    monkeypatch.setattr(tasks, "write_env_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "write_tool_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "run_streamed", lambda *_args, **_kwargs: 0)

    mk = repo_root / ".noraa" / "esmf" / "install" / "lib" / "esmf.mk"
    mk.parent.mkdir(parents=True)
    mk.write_text("ESMF")
    monkeypatch.setattr(tasks, "bootstrapped_esmf_mk", lambda _repo_root: mk)

    tasks.bootstrap_esmf(repo_root, "v8.6.1")

    text = capsys.readouterr().out
    assert "Run this command next: noraa bootstrap deps --repo" in text
    assert "Then run: noraa verify --repo" in text


def test_deps_preflight_reports_missing_pnetcdf_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tasks.shutil, "which", lambda *_args, **_kwargs: None)
    result = tasks._deps_preflight_failure(tmp_path, {"PATH": ""})
    assert result is not None
    msg, next_step = result
    assert "pnetcdf-config" in msg
    assert "sudo apt install -y pnetcdf-bin" == next_step


def test_deps_preflight_ok_when_pnetcdf_config_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tasks.shutil, "which", lambda *_args, **_kwargs: "/usr/bin/pnetcdf-config")
    assert tasks._deps_preflight_failure(tmp_path, {"PATH": "/usr/bin"}) is None
