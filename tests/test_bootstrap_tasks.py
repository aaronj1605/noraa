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
    assert "Next step: noraa bootstrap deps --repo" in text
    assert "Then run: noraa verify --repo" in text
