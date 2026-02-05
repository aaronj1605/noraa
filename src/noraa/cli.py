from __future__ import annotations

from pathlib import Path
import os
from shutil import which
import typer

from .util import git_root, log_dir, run_streamed, safe_check_output
from .snapshot import write_env_snapshot, write_tool_snapshot
from .validate import validate_mpas_success
from .agent.diagnose import diagnose_log
from .project import (
    ProjectConfig,
    load_project,
    write_project,
    validate_repo_origin,
    get_origin_url,
)

app = typer.Typer(add_completion=False)


def _target_repo(path: str) -> Path:
    return git_root(Path(path).resolve())


def _build_env(deps_prefix: str | None, esmf_mkfile: str | None) -> dict[str, str]:
    env = os.environ.copy()
    if deps_prefix:
        env["DEPS_PREFIX"] = deps_prefix
        env["CMAKE_PREFIX_PATH"] = deps_prefix
        env["PATH"] = f"{deps_prefix}/bin:" + env.get("PATH", "")
        env["LD_LIBRARY_PATH"] = f"{deps_prefix}/lib:{deps_prefix}/lib64:" + env.get("LD_LIBRARY_PATH", "")
    if esmf_mkfile:
        env["ESMFMKFILE"] = esmf_mkfile
    return env


def _require_project(repo_root: Path) -> ProjectConfig:
    cfg = load_project(repo_root)
    if cfg is None:
        raise SystemExit(
            "Missing .noraa/project.toml under the target repo.\n"
            f"Run: noraa init --repo {repo_root}"
        )
    ok, msg = validate_repo_origin(repo_root, cfg)
    if not ok:
        raise SystemExit(msg)
    return cfg


@app.command()
def init(
    repo: str = typer.Option(".", "--repo"),
    force: bool = typer.Option(False, "--force"),
    upstream_url: str = typer.Option("https://github.com/NOAA-EMC/ufsatm.git", "--upstream-url"),
):
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
    repo_root = _target_repo(repo)
    _require_project(repo_root)

    out = log_dir(repo_root, "doctor")
    env = os.environ.copy()
    write_env_snapshot(out, env)
    write_tool_snapshot(out, env)

    print((out / "tools.txt").read_text(), end="")
    print(f"Logs: {out}")


def _detect_verify_script(repo_root: Path) -> Path | None:
    candidates = [
        repo_root / "scripts" / "verify_mpas_smoke.sh",
        repo_root / "scripts" / "verify.sh",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


@app.command()
def verify(
    repo: str = typer.Option(".", "--repo"),
    deps_prefix: str = typer.Option(None, "--deps-prefix"),
    esmf_mkfile: str = typer.Option(None, "--esmf-mkfile"),
    clean: bool = typer.Option(True, "--clean/--no-clean"),
):
    repo_root = _target_repo(repo)
    cfg = _require_project(repo_root)

    out = log_dir(repo_root, "verify")
    env = _build_env(deps_prefix, esmf_mkfile)

    write_env_snapshot(out, env)
    write_tool_snapshot(out, env)

    script = Path(cfg.verify_script) if cfg.verify_script else None
    if not script or not script.exists():
        detected = _detect_verify_script(repo_root)
        if not detected:
            raise SystemExit(
                "No verify script found in this ufsatm checkout. "
                "NORAA does not run CMake fallback."
            )
        script = detected

    if clean:
        safe_check_output(["bash", "-lc", "rm -rf .noraa/build"], cwd=repo_root, env=env)

    rc = run_streamed(["bash", str(script)], repo_root, out, env)
    (out / "exit_code.txt").write_text(f"{rc}\n")

    v = validate_mpas_success(repo_root, deps_prefix, out)
    (out / "postcheck.txt").write_text(f"ok={v.ok}\nreason={v.reason}\n")

    if rc != 0 or not v.ok:
        code, msg, rule_id, script_text = diagnose_log(out, repo_root)
        (out / "diagnosis.txt").write_text(msg)
        print(msg, end="")
        raise SystemExit(code)

    print(f"VERIFY PASSED. Logs: {out}")


def main():
    app()


if __name__ == "__main__":
    main()
