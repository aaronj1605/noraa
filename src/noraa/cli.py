from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import typer

from .agent.diagnose import diagnose_log
from .bootstrap.tasks import bootstrap_deps, bootstrap_esmf
from .buildsystem.configure import cmake_fallback_mpas
from .buildsystem.env import build_env
from .buildsystem.paths import (
    detect_verify_script,
    resolve_deps_prefix,
    resolve_esmf_mkfile,
)
from .messages import fail, repo_cmd
from .project import (
    ProjectConfig,
    get_origin_url,
    load_project,
    validate_repo_origin,
    write_project,
)
from .snapshot import write_env_snapshot, write_tool_snapshot
from .util import git_root, log_dir, run_streamed, safe_check_output
from .validate import validate_mpas_success
from .workflow import guided_build, preflight, run_smoke

BASELINE_HELP = "Tested baseline: Linux (Ubuntu 22.04/24.04), Python 3.11+, upstream ufsatm develop.\nUse: noraa <command> --help for command details."

app = typer.Typer(add_completion=False, help=BASELINE_HELP)
run_smoke_app = typer.Typer(
    add_completion=False,
    help="Optional structured smoke-run helpers (readiness, data, execution).",
)


@app.callback()
def _root_callback() -> None:
    """Tested baseline: Linux (Ubuntu 22.04/24.04), Python 3.11+, upstream ufsatm develop.
    Use: noraa <command> --help for command details."""
    return None


app.add_typer(run_smoke_app, name="run-smoke")


def _target_repo(path: str) -> Path:
    return git_root(Path(path).resolve())


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


@app.command()
def init(
    repo: str = typer.Option(".", "--repo"),
    force: bool = typer.Option(False, "--force"),
    upstream_url: str = typer.Option(
        "https://github.com/NOAA-EMC/ufsatm.git", "--upstream-url"
    ),
):
    """Initialize NORAA for a target ufsatm checkout."""
    repo_root = _target_repo(repo)
    existing = load_project(repo_root)
    if existing and not force:
        raise SystemExit("Project already initialized. Use --force to overwrite.")

    origin = ""
    try:
        origin = get_origin_url(repo_root)
    except Exception:
        pass

    cfg = ProjectConfig(repo_path=str(repo_root), upstream_url=upstream_url)
    if origin and origin.rstrip("/") != upstream_url.rstrip("/"):
        if not typer.confirm("Repo is a fork. Proceed anyway?", default=False):
            raise SystemExit(2)
        cfg.allow_fork = True
        cfg.fork_url = origin

    write_project(repo_root, cfg)
    print("Initialized NORAA project.")


@app.command()
def doctor(repo: str = typer.Option(".", "--repo")):
    """Capture environment and tool snapshots for the target repo."""
    repo_root = _target_repo(repo)
    _require_project(repo_root)

    out = log_dir(repo_root, "doctor")
    env = os.environ.copy()
    write_env_snapshot(out, env)
    write_tool_snapshot(out, env)

    print((out / "tools.txt").read_text(), end="")
    print(f"Logs: {out}")


@app.command()
def verify(
    repo: str = typer.Option(".", "--repo"),
    deps_prefix: str = typer.Option(None, "--deps-prefix"),
    esmf_mkfile: str = typer.Option(None, "--esmf-mkfile"),
    clean: bool = typer.Option(True, "--clean/--no-clean"),
    preflight_only: bool = typer.Option(False, "--preflight-only"),
):
    """
    Verify that MPAS can be configured and built for the target ufsatm repo.

    Behaviour:
    - Require ESMF (bootstrapped under .noraa/esmf, --esmf-mkfile, or --deps-prefix).
    - Prefer upstream verify scripts when present; otherwise fall back to CMake.
    - Always build MPAS only (-DMPAS=ON -DFV3=OFF) with a valid MPAS CCPP suite.
    """
    repo_root = _target_repo(repo)
    cfg = _require_project(repo_root)
    resolved_deps = resolve_deps_prefix(repo_root, deps_prefix)
    script = Path(cfg.verify_script) if cfg.verify_script else None
    if not script or not script.exists():
        script = detect_verify_script(repo_root)
    issues = _verify_preflight_issues(
        repo_root,
        deps_prefix=resolved_deps,
        esmf_mkfile=esmf_mkfile,
        using_verify_script=bool(script and script.exists()),
    )
    if preflight_only:
        if issues:
            raise SystemExit(_format_preflight_summary(issues))
        print("Preflight OK. No blocking issues found.")
        return
    if issues:
        raise SystemExit(_format_preflight_summary(issues))
    out = log_dir(repo_root, "verify")

    resolved_esmf = resolve_esmf_mkfile(repo_root, resolved_deps, esmf_mkfile)
    env = build_env(resolved_deps, resolved_esmf)

    write_env_snapshot(out, env)
    write_tool_snapshot(out, env)

    if script and script.exists():
        if clean:
            safe_check_output(["bash", "-lc", "rm -rf .noraa/build"], cwd=repo_root, env=env)
        rc = run_streamed(["bash", str(script)], repo_root, out, env)
    else:
        rc = cmake_fallback_mpas(
            repo_root=repo_root,
            out=out,
            env=env,
            clean=clean,
            deps_prefix=resolved_deps,
            esmf_mkfile=resolved_esmf,
            python_executable=sys.executable,
        )

    (out / "exit_code.txt").write_text(f"{rc}\n")
    v = validate_mpas_success(repo_root, resolved_deps, out)
    (out / "postcheck.txt").write_text(f"ok={v.ok}\nreason={v.reason}\n")

    if rc != 0 or not v.ok:
        code, msg, _, _ = diagnose_log(
            out, repo_root, deps_prefix=resolved_deps, esmf_mkfile=resolved_esmf
        )
        (out / "diagnosis.txt").write_text(msg)
        print(msg, end="")
        print(f"\nNext step: noraa diagnose --repo {repo_root} --log-dir {out}")
        raise SystemExit(code)

    print(f"VERIFY PASSED. Logs: {out}")


@app.command()
def bootstrap(
    repo: str = typer.Option(".", "--repo"),
    component: str = typer.Argument(...),
    esmf_branch: str = typer.Option(
        "v8.6.1",
        "--esmf-branch",
        help="ESMF git branch or tag to use when bootstrapping.",
    ),
):
    """
    Bootstrap required components under .noraa/ in the target repo.

    Supported components:
      - esmf -> clone, build, and install ESMF into .noraa/esmf/install
      - deps -> build bacio/bufr/sp/w3emc/pio into .noraa/deps/install
    """
    repo_root = _target_repo(repo)
    _require_project(repo_root)

    if component == "esmf":
        bootstrap_esmf(repo_root, esmf_branch)
        return
    if component == "deps":
        bootstrap_deps(repo_root)
        return

    fail(
        f"Unsupported bootstrap component: {component}",
        next_step=(
            f"{repo_cmd(repo_root, 'bootstrap', 'deps')}  or  "
            f"{repo_cmd(repo_root, 'bootstrap', 'esmf')}"
        ),
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


@app.command("build-mpas")
def build_mpas(
    repo: str = typer.Option(".", "--repo"),
    clean: bool = typer.Option(True, "--clean/--no-clean"),
    yes: bool = typer.Option(
        False, "--yes", help="Auto-accept guided prompts and run all steps."
    ),
    esmf_branch: str = typer.Option(
        "v8.6.1",
        "--esmf-branch",
        help="ESMF git branch or tag to use if ESMF bootstrap is required.",
    ),
):
    """
    Guided one-command MPAS build path for a target ufsatm checkout.
    """
    repo_root = _target_repo(repo)
    guided_build.run_build_mpas(
        repo_root=repo_root,
        clean=clean,
        yes=yes,
        esmf_branch=esmf_branch,
        confirm_fn=lambda msg: typer.confirm(msg, default=True),
        init_project_fn=lambda p: init(
            repo=str(p),
            force=False,
            upstream_url="https://github.com/NOAA-EMC/ufsatm.git",
        ),
        require_project_fn=_require_project,
        verify_fn=lambda p, do_clean: verify(
            repo=str(p),
            deps_prefix=None,
            esmf_mkfile=None,
            clean=do_clean,
            preflight_only=False,
        ),
    )


@run_smoke_app.command("status")
def run_smoke_status(repo: str = typer.Option(".", "--repo")):
    """Report readiness for optional run-smoke workflows with RED/GREEN checks."""
    repo_root = _target_repo(repo)
    checks = run_smoke.collect_status_checks(repo_root)
    report, _ = run_smoke.format_status_report(checks)
    print(report)


@app.command()
def diagnose(
    repo: str = typer.Option(".", "--repo"),
    log_dir_override: str = typer.Option(
        None,
        "--log-dir",
        help="Explicit log directory to diagnose (defaults to latest verify run).",
    ),
):
    """
    Run rule-based diagnosis on a previous NORAA log directory.

    By default, this inspects the most recent verify log under .noraa/logs.
    """
    repo_root = _target_repo(repo)
    _require_project(repo_root)

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

    code, msg, _, _ = diagnose_log(log_dir_path, repo_root, deps_prefix=None, esmf_mkfile=None)
    print(msg, end="")
    raise SystemExit(code)


def _cmake_version() -> tuple[int, int, int] | None:
    return preflight.cmake_version()


def _verify_preflight_issues(
    repo_root: Path,
    *,
    deps_prefix: str | None,
    esmf_mkfile: str | None,
    using_verify_script: bool,
) -> list[tuple[str, str]]:
    return preflight.verify_preflight_issues(
        repo_root,
        deps_prefix=deps_prefix,
        esmf_mkfile=esmf_mkfile,
        using_verify_script=using_verify_script,
        cmake_version_fn=_cmake_version,
    )


def _verify_preflight_failure(
    repo_root: Path,
    *,
    deps_prefix: str | None,
    esmf_mkfile: str | None,
    using_verify_script: bool,
) -> tuple[str, str] | None:
    issues = _verify_preflight_issues(
        repo_root,
        deps_prefix=deps_prefix,
        esmf_mkfile=esmf_mkfile,
        using_verify_script=using_verify_script,
    )
    if not issues:
        return None
    return issues[0]


def _format_preflight_failure(issue: str, action: str) -> str:
    return f"{issue}\nAction required: {action}"


def _format_preflight_summary(issues: list[tuple[str, str]]) -> str:
    return preflight.format_preflight_summary(issues)


def _python_runtime_error() -> str | None:
    return preflight.python_runtime_error()


def main():
    err = _python_runtime_error()
    if err:
        raise SystemExit(err)
    app()


if __name__ == "__main__":
    main()
