from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from .agent.diagnose import diagnose_log
from .messages import fail, repo_cmd
from .fallbacks.fv3 import (
    _apply_fv3_external_sst_fallback,
    _apply_fv3_fms_r8_fallback,
    _apply_fv3_fms_required_fallback,
    _apply_fv3_fv_dynamics_kind_fix,
    _apply_fv3_stochastic_wrapper_stub,
    _apply_fv3_stochy_pattern_fallback,
    _apply_fv3_top_level_dependency_guards,
    _apply_fv3_update_ca_fallback,
)
from .project import (
    ProjectConfig,
    load_project,
    validate_repo_origin,
)
from .util import git_root
from .workflow import guided_build, preflight
from .workflow.core_commands import register_core_commands
from .workflow.run_smoke_rt_commands import register_run_smoke_and_rt_commands

BASELINE_HELP = "Tested baseline: Linux (Ubuntu 22.04/24.04), Python 3.11+, upstream ufsatm develop.\nUse: noraa <command> --help for command details."

app = typer.Typer(add_completion=False, help=BASELINE_HELP)
run_smoke_app = typer.Typer(
    add_completion=False,
    help="Optional structured smoke-run helpers (readiness, data, execution).",
)
run_smoke_fetch_app = typer.Typer(
    add_completion=False,
    help="Pull smoke-run sample data from repo scan, official catalog, or local files.",
)
rt_app = typer.Typer(
    add_completion=False,
    help="Guided Runtime-Test (RT-style) walkthrough for end-to-end runs.",
)


@app.callback()
def _root_callback() -> None:
    """Tested baseline: Linux (Ubuntu 22.04/24.04), Python 3.11+, upstream ufsatm develop.
    Use: noraa <command> --help for command details."""
    return None


app.add_typer(run_smoke_app, name="run-smoke")
run_smoke_app.add_typer(run_smoke_fetch_app, name="fetch-data")
app.add_typer(rt_app, name="rt")


def _target_repo(path: str) -> Path:
    p = Path(path).expanduser().resolve()
    try:
        return git_root(p)
    except Exception:
        fail(
            f"Target repo is not a valid git checkout: {p}",
            next_step="Use --repo pointing to your ufsatm git root (example: /home/user/work/ufsatm)",
        )


def _normalize_core(core: str) -> str:
    value = (core or "mpas").strip().lower()
    if value not in {"mpas", "fv3"}:
        fail(
            f"Unsupported core: {core}",
            next_step="Use --core mpas or --core fv3",
        )
    return value


def _ensure_pytest_available() -> None:
    if importlib.util.find_spec("pytest") is None:
        fail(
            "pytest is required for noraa self-test but is not installed in this environment.",
            next_step=f"{sys.executable} -m pip install pytest",
        )


def _noraa_source_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "src" / "noraa").exists():
            return parent
    return None


def _self_test_repo_root(repo: str | None) -> Path:
    if repo:
        return _target_repo(repo)
    repo_root = _noraa_source_root()
    if repo_root is None:
        fail(
            "NORAA self-test requires a source checkout with tests.",
            next_step="Reinstall from a local NORAA checkout with `pip install -e .` or pass --repo /path/to/noraa",
        )
    return repo_root


def _require_project(repo_root: Path) -> ProjectConfig:
    cfg = load_project(repo_root)
    if cfg is None:
        fail(
            "Missing .noraa/project.toml under the target repo.",
            next_step=repo_cmd(repo_root, "init"),
        )
    ok, msg = validate_repo_origin(repo_root, cfg)
    if not ok:
        raise SystemExit(msg)
    return cfg


verify_runner, _core_cmds = register_core_commands(
    app=app,
    target_repo=_target_repo,
    require_project=_require_project,
    normalize_core=_normalize_core,
)

register_run_smoke_and_rt_commands(
    run_smoke_app=run_smoke_app,
    run_smoke_fetch_app=run_smoke_fetch_app,
    rt_app=rt_app,
    target_repo=_target_repo,
    require_project=_require_project,
    normalize_core=_normalize_core,
    verify_runner=verify_runner,
)


def _confirm_or_fail(
    *, prompt: str, assume_yes: bool, failure_message: str, next_step: str
) -> None:
    guided_build.confirm_or_fail(
        prompt=prompt,
        assume_yes=assume_yes,
        failure_message=failure_message,
        next_step=next_step,
        confirm_fn=lambda msg: typer.confirm(msg, default=True),
    )


def init(
    repo: str = ".",
    force: bool = False,
    core: str = "mpas",
    upstream_url: str = "https://github.com/NOAA-EMC/ufsatm.git",
):
    return _core_cmds["init"](repo=repo, force=force, core=core, upstream_url=upstream_url)


def doctor(repo: str = "."):
    return _core_cmds["doctor"](repo=repo)


def verify(
    repo: str = ".",
    core: str | None = None,
    deps_prefix: str | None = None,
    esmf_mkfile: str | None = None,
    clean: bool = True,
    preflight_only: bool = False,
    fv3_fms_r8_fallback: bool = True,
):
    return _core_cmds["verify"](
        repo=repo,
        core=core,
        deps_prefix=deps_prefix,
        esmf_mkfile=esmf_mkfile,
        clean=clean,
        preflight_only=preflight_only,
        fv3_fms_r8_fallback=fv3_fms_r8_fallback,
    )


def bootstrap(repo: str = ".", component: str = "", esmf_branch: str = "v8.6.1"):
    return _core_cmds["bootstrap"](repo=repo, component=component, esmf_branch=esmf_branch)


def build(
    repo: str = ".",
    core: str | None = None,
    clean: bool = True,
    yes: bool = False,
    esmf_branch: str = "v8.6.1",
):
    repo_root = _target_repo(repo)
    cfg = load_project(repo_root)
    selected_core = _normalize_core(core or (cfg.core if cfg else "mpas"))
    guided_build.run_build_core(
        repo_root=repo_root,
        clean=clean,
        yes=yes,
        esmf_branch=esmf_branch,
        core=selected_core,
        confirm_fn=lambda msg: typer.confirm(msg, default=True),
        init_project_fn=lambda p: init(
            repo=str(p),
            force=False,
            core=selected_core,
            upstream_url="https://github.com/NOAA-EMC/ufsatm.git",
        ),
        require_project_fn=_require_project,
        verify_fn=lambda p, do_clean, c: verify(
            repo=str(p),
            core=c,
            deps_prefix=None,
            esmf_mkfile=None,
            clean=do_clean,
            preflight_only=False,
        ),
    )


def build_mpas(
    repo: str = ".",
    clean: bool = True,
    yes: bool = False,
    esmf_branch: str = "v8.6.1",
):
    return build(repo=repo, core="mpas", clean=clean, yes=yes, esmf_branch=esmf_branch)


def _cmake_version() -> tuple[int, int, int] | None:
    return preflight.cmake_version()


def _verify_preflight_issues(
    repo_root: Path,
    *,
    deps_prefix: str | None,
    esmf_mkfile: str | None,
    using_verify_script: bool,
    core: str,
) -> list[tuple[str, str]]:
    return preflight.verify_preflight_issues(
        repo_root,
        deps_prefix=deps_prefix,
        esmf_mkfile=esmf_mkfile,
        using_verify_script=using_verify_script,
        core=core,
        cmake_version_fn=_cmake_version,
    )


def _verify_preflight_failure(
    repo_root: Path,
    *,
    deps_prefix: str | None,
    esmf_mkfile: str | None,
    using_verify_script: bool,
    core: str,
) -> tuple[str, str] | None:
    issues = _verify_preflight_issues(
        repo_root,
        deps_prefix=deps_prefix,
        esmf_mkfile=esmf_mkfile,
        using_verify_script=using_verify_script,
        core=core,
    )
    if not issues:
        return None
    return issues[0]


def _format_preflight_failure(issue: str, action: str) -> str:
    return f"{issue}\nAction required: {action}"


def _format_preflight_summary(issues: list[tuple[str, str]]) -> str:
    return preflight.format_preflight_summary(issues)


@app.command()
def diagnose(
    repo: str = typer.Option(".", "--repo", metavar="REPO_PATH"),
    log_dir_override: str = typer.Option(
        None,
        "--log-dir",
        metavar="LOG_DIR",
        help="Explicit log directory to diagnose (defaults to latest verify run).",
    ),
):
    """
    Run rule-based diagnosis on a previous NORAA log directory.

    By default, this inspects the most recent verify log under .noraa/logs.
    """
    repo_root = _target_repo(repo)
    cfg = _require_project(repo_root)

    if log_dir_override:
        log_dir_path = Path(log_dir_override)
    else:
        logs_root = repo_root / ".noraa" / "logs"
        if not logs_root.exists():
            fail(
                "No .noraa/logs directory found to diagnose.",
                next_step=repo_cmd(repo_root, "verify"),
            )
        candidates = sorted(
            p for p in logs_root.iterdir() if p.is_dir() and p.name.endswith("-verify")
        )
        if not candidates:
            fail(
                "No verify logs found under .noraa/logs to diagnose.",
                next_step=repo_cmd(repo_root, "verify"),
            )
        log_dir_path = candidates[-1]

    code, msg, _, _ = diagnose_log(
        log_dir_path,
        repo_root,
        deps_prefix=None,
        esmf_mkfile=None,
        core=cfg.core,
    )
    print(msg, end="")
    raise SystemExit(code)


@app.command("self-test")
def self_test(
    repo: str | None = typer.Option(
        None,
        "--repo",
        metavar="REPO_PATH",
        help="Optional NORAA source checkout to test. Defaults to the installed NORAA checkout.",
    ),
    pytest_args: str = typer.Option(
        "-q",
        "--pytest-args",
        metavar="PYTEST_ARGS",
        help="Arguments forwarded to pytest (quoted string).",
    ),
):
    """Run local NORAA tests for the installed or explicitly provided checkout."""
    repo_root = _self_test_repo_root(repo)
    _ensure_pytest_available()
    args = shlex.split(pytest_args, posix=(os.name != "nt"))
    cmd = [sys.executable, "-m", "pytest", *args]
    rc = subprocess.run(cmd, cwd=str(repo_root), text=True, check=False).returncode
    if rc != 0:
        fail(
            "NORAA self-test failed.",
            next_step=f"{repo_cmd(repo_root, 'self-test')} --pytest-args \"{pytest_args}\"",
        )
    print("NORAA self-test passed.")


def _python_runtime_error() -> str | None:
    return preflight.python_runtime_error()


def main():
    err = _python_runtime_error()
    if err:
        raise SystemExit(err)
    app()


if __name__ == "__main__":
    main()
