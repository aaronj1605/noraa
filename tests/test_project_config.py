from __future__ import annotations

from noraa.project import ProjectConfig, load_project, write_project


def test_project_config_persists_core(tmp_path) -> None:
    cfg = ProjectConfig(
        repo_path=str(tmp_path),
        upstream_url="https://github.com/NOAA-EMC/ufsatm.git",
        core="fv3",
        verify_script="scripts/verify_fv3_smoke.sh",
    )
    write_project(tmp_path, cfg)
    loaded = load_project(tmp_path)
    assert loaded is not None
    assert loaded.core == "fv3"
    assert loaded.verify_script == "scripts/verify_fv3_smoke.sh"
