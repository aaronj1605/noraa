from __future__ import annotations

from pathlib import Path

from noraa.agent.diagnose import diagnose_log


def _write_rule_hit(log_dir: Path) -> None:
    log_dir.mkdir(parents=True)
    (log_dir / "stdout.txt").write_text("undefined reference to ompi_op_set_cxx_callback\n")
    (log_dir / "stderr.txt").write_text("")


def test_diagnose_uses_fv3_guidance(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    log_dir = tmp_path / "logs" / "run"
    _write_rule_hit(log_dir)
    code, msg, _, _ = diagnose_log(log_dir, repo_root, core="fv3")
    assert code == 2
    assert "noraa verify --repo" in msg
    assert "--core fv3" in msg
    assert "scripts/verify_mpas_smoke.sh" not in msg
    assert "ufs_model" in msg


def test_diagnose_uses_mpas_guidance(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    log_dir = tmp_path / "logs" / "run"
    _write_rule_hit(log_dir)
    code, msg, _, _ = diagnose_log(log_dir, repo_root, core="mpas")
    assert code == 2
    assert "noraa verify --repo" in msg
    assert "--core mpas" in msg
    assert "mpas_atmosphere" in msg


def test_diagnose_prefers_fms_r4_missing_rule(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    log_dir = tmp_path / "logs" / "run"
    log_dir.mkdir(parents=True)
    (log_dir / "stdout.txt").write_text(
        "CMake Error: FMS::fms_r4 [COMPONENT R4 NOT FOUND]\n"
    )
    (log_dir / "stderr.txt").write_text("")
    code, msg, rule_id, _ = diagnose_log(log_dir, repo_root, core="fv3")
    assert code == 2
    assert rule_id == "fms-r4-missing"
    assert "FMS R4 component missing" in msg
    assert "--core fv3" in msg


def test_diagnose_matches_stochastic_target_missing_rule(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    log_dir = tmp_path / "logs" / "run"
    log_dir.mkdir(parents=True)
    (log_dir / "stdout.txt").write_text(
        'The dependency target "stochastic_physics" of target "ufsatm_fv3" does not exist.\n'
    )
    (log_dir / "stderr.txt").write_text("")
    code, msg, rule_id, _ = diagnose_log(log_dir, repo_root, core="fv3")
    assert code == 2
    assert rule_id == "fv3-stochastic-physics-target-missing"
    assert "stochastic_physics" in msg
