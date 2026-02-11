from __future__ import annotations

import os
import subprocess
import shutil
import sys
from pathlib import Path

import typer

from .agent.diagnose import diagnose_log
from .bootstrap.tasks import bootstrap_deps, bootstrap_esmf
from .buildsystem.configure import cmake_fallback_mpas
from .buildsystem.env import build_env
from .buildsystem.paths import (
    bootstrapped_deps_prefix,
    bootstrapped_esmf_mk,
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

BASELINE_HELP = "Tested baseline: Linux (Ubuntu 22.04/24.04), Python 3.11+, upstream ufsatm develop.\nUse: noraa <command> --help for command details."

app = typer.Typer(add_completion=False, help=BASELINE_HELP)


@app.callback()
def _root_callback() -> None:
    """Tested baseline: Linux (Ubuntu 22.04/24.04), Python 3.11+, upstream ufsatm develop.
    Use: noraa <command> --help for command details."""
    return None


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
    if assume_yes or typer.confirm(prompt, default=True):
        return
    fail(failure_message, next_step=next_step)


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
    print(f"NORAA guided MPAS build for: {repo_root}")

    if load_project(repo_root) is None:
        _confirm_or_fail(
            prompt="Project is not initialized. Run noraa init now?",
            assume_yes=yes,
            failure_message="Project initialization is required before guided build.",
            next_step=repo_cmd(repo_root, "init"),
        )
        init(
            repo=str(repo_root),
            force=False,
            upstream_url="https://github.com/NOAA-EMC/ufsatm.git",
        )
    _require_project(repo_root)

    ccpp_prebuild = repo_root / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    if not ccpp_prebuild.exists():
        _confirm_or_fail(
            prompt="Required submodule content is missing. Run git submodule update --init --recursive now?",
            assume_yes=yes,
            failure_message=f"Required CCPP submodule content is missing: {ccpp_prebuild}",
            next_step="git submodule update --init --recursive",
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
        print("Fix implemented: initialized required git submodules.")

    if bootstrapped_esmf_mk(repo_root) is None:
        _confirm_or_fail(
            prompt="ESMF is missing under .noraa/esmf/install. Bootstrap ESMF now?",
            assume_yes=yes,
            failure_message="Issue identified: ESMF is required before verify can run.",
            next_step=repo_cmd(repo_root, "bootstrap", "esmf"),
        )
        bootstrap_esmf(repo_root, esmf_branch)
        print("Fix implemented: bootstrapped ESMF under .noraa/esmf/install.")

    if bootstrapped_deps_prefix(repo_root) is None:
        _confirm_or_fail(
            prompt="MPAS dependency bundle is missing under .noraa/deps/install. Bootstrap deps now?",
            assume_yes=yes,
            failure_message="Issue identified: MPAS dependency bundle is required before verify can run.",
            next_step=repo_cmd(repo_root, "bootstrap", "deps"),
        )
        bootstrap_deps(repo_root)
        print("Fix implemented: bootstrapped MPAS dependency bundle under .noraa/deps/install.")

    print("Running verify (MPAS only)...")
    verify(
        repo=str(repo_root),
        deps_prefix=None,
        esmf_mkfile=None,
        clean=clean,
        preflight_only=False,
    )


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
    try:
        out = subprocess.check_output(["cmake", "--version"], text=True)
    except Exception:
        return None
    first = out.splitlines()[0] if out else ""
    marker = "version "
    if marker not in first:
        return None
    v = first.split(marker, 1)[1].strip().split()[0]
    parts = v.split(".")
    if len(parts) < 2:
        return None
    major = int(parts[0])
    minor = int(parts[1])
    patch = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    return (major, minor, patch)


def _verify_preflight_issues(
    repo_root: Path,
    *,
    deps_prefix: str | None,
    esmf_mkfile: str | None,
    using_verify_script: bool,
) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []

    ccpp_prebuild = repo_root / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    if not ccpp_prebuild.exists():
        issues.append(
            (
                f"Issue identified: Required CCPP submodule content is missing: {ccpp_prebuild}",
                "git submodule update --init --recursive",
            )
        )

    explicit_mk = Path(esmf_mkfile) if esmf_mkfile else None
    deps_mk = Path(deps_prefix) / "lib" / "esmf.mk" if deps_prefix else None
    if not (
        (explicit_mk and explicit_mk.exists())
        or bootstrapped_esmf_mk(repo_root)
        or (deps_mk and deps_mk.exists())
    ):
        issues.append(
            (
                "Issue identified: ESMF not found (missing esmf.mk under .noraa/esmf/install and no valid --esmf-mkfile/--deps-prefix).",
                repo_cmd(repo_root, "bootstrap", "esmf"),
            )
        )

    if using_verify_script:
        return issues

    deps_root = Path(deps_prefix) if deps_prefix else repo_root / ".noraa" / "deps" / "install"
    if not deps_root.exists():
        issues.append(
            (
                f"Issue identified: MPAS dependency bundle not found: {deps_root}",
                repo_cmd(repo_root, "bootstrap", "deps"),
            )
        )

    if shutil.which("pnetcdf-config") is None:
        issues.append(
            (
                "Issue identified: pnetcdf-config not found (needed for noraa bootstrap deps on clean systems).",
                "sudo apt install -y pnetcdf-bin",
            )
        )

    version = _cmake_version()
    if version is None:
        issues.append(
            (
                "Issue identified: CMake is required for verify fallback but was not found in PATH.",
                "pip install -U 'cmake>=3.28'",
            )
        )
    elif version < (3, 28, 0):
        issues.append(
            (
                f"Issue identified: CMake >= 3.28 is required for verify fallback (found {version[0]}.{version[1]}.{version[2]}).",
                "pip install -U 'cmake>=3.28'",
            )
        )
    return issues


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
    lines = ["Preflight identified blocking issues:"]
    for issue, action in issues:
        lines.append(issue)
        lines.append(f"Action required: {action}")
    return "\n".join(lines)


def _python_runtime_error() -> str | None:
    if sys.version_info >= (3, 11):
        return None
    return (
        "Python 3.11+ is required for noraa. "
        "On Ubuntu 22.04, install python3.11 and python3.11-venv, "
        "recreate your virtual environment, and reinstall noraa."
    )


def main():
    err = _python_runtime_error()
    if err:
        raise SystemExit(err)
    app()


if __name__ == "__main__":
    main()
