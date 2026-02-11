from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from ..bootstrap.tasks import bootstrap_deps, bootstrap_esmf
from ..buildsystem.paths import bootstrapped_deps_prefix, bootstrapped_esmf_mk
from ..messages import fail, repo_cmd
from ..project import load_project
from ..snapshot import write_env_snapshot, write_tool_snapshot
from ..ui import notice, summary
from ..util import log_dir, run_streamed


def confirm_or_fail(
    *,
    prompt: str,
    assume_yes: bool,
    failure_message: str,
    next_step: str,
    confirm_fn: Callable[[str], bool],
) -> None:
    if assume_yes or confirm_fn(prompt):
        return
    fail(failure_message, next_step=next_step)


def run_build_mpas(
    *,
    repo_root: Path,
    clean: bool,
    yes: bool,
    esmf_branch: str,
    confirm_fn: Callable[[str], bool],
    init_project_fn: Callable[[Path], None],
    require_project_fn: Callable[[Path], None],
    verify_fn: Callable[[Path, bool], None],
) -> None:
    notice(
        "NORAA Guided Build",
        [
            f"Repository: {repo_root}",
            "Goal: initialize, resolve blockers, and verify MPAS build.",
        ],
    )
    fixes: list[str] = []

    if load_project(repo_root) is None:
        notice(
            "Issue Identified",
            ["Project is not initialized for NORAA management."],
        )
        confirm_or_fail(
            prompt="Project is not initialized. Run noraa init now?",
            assume_yes=yes,
            failure_message="Project initialization is required before guided build.",
            next_step=repo_cmd(repo_root, "init"),
            confirm_fn=confirm_fn,
        )
        init_project_fn(repo_root)
        fixes.append("Initialized NORAA project metadata (.noraa/project.toml).")
    require_project_fn(repo_root)

    ccpp_prebuild = repo_root / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    if not ccpp_prebuild.exists():
        notice(
            "Issue Identified",
            [f"Required submodule content is missing: {ccpp_prebuild}"],
        )
        confirm_or_fail(
            prompt="Required submodule content is missing. Run git submodule update --init --recursive now?",
            assume_yes=yes,
            failure_message=f"Required CCPP submodule content is missing: {ccpp_prebuild}",
            next_step="git submodule update --init --recursive",
            confirm_fn=confirm_fn,
        )
        out = log_dir(repo_root, "build-mpas-submodules")
        env = os.environ.copy()
        write_env_snapshot(out, env)
        write_tool_snapshot(out, env)
        rc_submodule = run_streamed(
            ["git", "submodule", "update", "--init", "--recursive"], repo_root, out, env
        )
        (out / "exit_code.txt").write_text(f"{rc_submodule}\n")
        if rc_submodule != 0:
            fail(
                "Submodule update failed during guided build.",
                logs=out,
                next_step="git submodule update --init --recursive",
            )
        fixes.append("Initialized required git submodules.")
        notice("Fix Implemented", [fixes[-1]])

    if bootstrapped_esmf_mk(repo_root) is None:
        notice(
            "Issue Identified",
            ["ESMF dependency is missing under .noraa/esmf/install."],
        )
        confirm_or_fail(
            prompt="ESMF is missing under .noraa/esmf/install. Bootstrap ESMF now?",
            assume_yes=yes,
            failure_message="Issue identified: ESMF is required before verify can run.",
            next_step=repo_cmd(repo_root, "bootstrap", "esmf"),
            confirm_fn=confirm_fn,
        )
        bootstrap_esmf(repo_root, esmf_branch)
        fixes.append("Bootstrapped ESMF under .noraa/esmf/install.")
        notice("Fix Implemented", [fixes[-1]])

    if bootstrapped_deps_prefix(repo_root) is None:
        notice(
            "Issue Identified",
            ["MPAS dependency bundle is missing under .noraa/deps/install."],
        )
        confirm_or_fail(
            prompt="MPAS dependency bundle is missing under .noraa/deps/install. Bootstrap deps now?",
            assume_yes=yes,
            failure_message="Issue identified: MPAS dependency bundle is required before verify can run.",
            next_step=repo_cmd(repo_root, "bootstrap", "deps"),
            confirm_fn=confirm_fn,
        )
        bootstrap_deps(repo_root)
        fixes.append("Bootstrapped MPAS dependency bundle under .noraa/deps/install.")
        notice("Fix Implemented", [fixes[-1]])

    notice("NORAA Action", ["Running verify (MPAS only)..."])
    verify_fn(repo_root, clean)
    summary(
        fixes=fixes,
        next_step=repo_cmd(repo_root, "run-smoke", "status"),
    )
