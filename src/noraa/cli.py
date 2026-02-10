from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from .agent.diagnose import diagnose_log
from .bootstrap.tasks import bootstrap_deps, bootstrap_esmf
from .buildsystem.cmake_fallback import cmake_fallback_mpas
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

app = typer.Typer(add_completion=False)


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
    out = log_dir(repo_root, "verify")

    resolved_deps = resolve_deps_prefix(repo_root, deps_prefix)
    resolved_esmf = resolve_esmf_mkfile(repo_root, resolved_deps, esmf_mkfile)
    env = build_env(resolved_deps, resolved_esmf)

    write_env_snapshot(out, env)
    write_tool_snapshot(out, env)

    script = Path(cfg.verify_script) if cfg.verify_script else None
    if not script or not script.exists():
        script = detect_verify_script(repo_root)

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


def main():
    app()


if __name__ == "__main__":
    main()

