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
            "Run:\n"
            f"  noraa init --repo {repo_root}\n"
        )
    ok, msg = validate_repo_origin(repo_root, cfg)
    if not ok:
        raise SystemExit(
            "Repo remote validation failed.\n"
            f"{msg}\n\n"
            "If you intended to use a fork, run:\n"
            f"  noraa init --repo {repo_root} --force\n"
        )
    return cfg


@app.command()
def init(
    repo: str = typer.Option(".", "--repo"),
    force: bool = typer.Option(False, "--force"),
    upstream_url: str = typer.Option("https://github.com/NOAA-EMC/ufsatm.git", "--upstream-url"),
):
    repo_root = _target_repo(repo)

    existing = load_project(repo_root)
    if existing is not None and not force:
        p = repo_root / ".noraa" / "project.toml"
        print(f"Project file already exists: {p}")
        print("Use --force to overwrite.")
        raise SystemExit(0)

    origin = ""
    try:
        origin = get_origin_url(repo_root)
    except Exception:
        origin = ""

    cfg = ProjectConfig(repo_path=str(repo_root), upstream_url=upstream_url)

    if origin and origin.rstrip("/") != upstream_url.rstrip("/"):
        print("Detected target repo origin does not match the official upstream.")
        print(f"  detected origin: {origin}")
        print(f"  official upstream: {upstream_url}")
        use_fork = typer.confirm("Do you want to proceed using a fork?", default=True)
        if not use_fork:
            print("No changes made.")
            print("To point this repo to upstream, run:")
            print(f"  cd {repo_root}")
            print(f"  git remote set-url origin {upstream_url}")
            raise SystemExit(2)

        fork_url = typer.prompt("Paste your fork URL (or press Enter to use detected origin)", default=origin)
        cfg.allow_fork = True
        cfg.fork_url = fork_url

    p = write_project(repo_root, cfg)
    print(f"Wrote {p}")


@app.command()
def doctor(repo: str = typer.Option(".", "--repo")):
    repo_root = _target_repo(repo)
    _ = _require_project(repo_root)

    out = log_dir(repo_root, "doctor")
    env = os.environ.copy()
    write_env_snapshot(out, env)
    write_tool_snapshot(out, env)

    print((out / "tools.txt").read_text(), end="")

    tools_txt = (out / "tools.txt").read_text()
    if "mpiexec: /usr/bin/mpiexec" in tools_txt or "mpicc: /usr/bin/mpicc" in tools_txt:
        print("\nNOTE: System MPI detected in PATH.")
        print("If you have a deps prefix, run verify like this:")
        print(
            "  noraa verify --repo " + str(repo_root) + " \\\n"
            "    --deps-prefix /path/to/deps \\\n"
            '    --esmf-mkfile "/path/to/esmf.mk"\n'
        )

    print(f"Logs: {out}")
    status = (out / "snapshot_status.txt").read_text().strip()
    raise SystemExit(0 if status == "ok" else 2)


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

    script = repo_root / cfg.verify_script
    if not script.exists():
        raise SystemExit(f"Missing verify script: {script}")

    if clean:
        safe_check_output(["bash", "-lc", "rm -rf .noraa/build"], cwd=repo_root, env=env)

    rc = run_streamed(["bash", str(script)], repo_root, out, env)
    (out / "exit_code.txt").write_text(f"{rc}\n")

    v = validate_mpas_success(repo_root, deps_prefix, out)
    (out / "postcheck.txt").write_text(f"ok={v.ok}\nreason={v.reason}\n")

    if rc != 0 or not v.ok:
        code, msg, rule_id, script_text = diagnose_log(
            out,
            repo_root,
            deps_prefix=deps_prefix,
            esmf_mkfile=esmf_mkfile or env.get("ESMFMKFILE"),
        )
        (out / "diagnosis.txt").write_text(msg)

        fix_path = None
        if rule_id and script_text:
            fix_path = out / f"fix_{rule_id}.sh"
            fix_path.write_text(script_text)
            os.chmod(fix_path, 0o755)

        print(f"VERIFY FAILED. Logs: {out}")
        if fix_path:
            print(f"Generated fix script: {fix_path}")
            print(f"Run it with: bash {fix_path}")
        print(msg, end="")
        raise SystemExit(code)

    print(f"VERIFY PASSED. Logs: {out}")


@app.command()
def diagnose(
    repo: str = typer.Option(".", "--repo"),
    log_dir_path: str = typer.Option(None, "--log-dir"),
    deps_prefix: str = typer.Option(None, "--deps-prefix"),
    esmf_mkfile: str = typer.Option(None, "--esmf-mkfile"),
):
    repo_root = _target_repo(repo)
    _ = _require_project(repo_root)

    if log_dir_path:
        ld = Path(log_dir_path).resolve()
    else:
        logs_root = repo_root / ".noraa" / "logs"
        if not logs_root.exists():
            raise SystemExit("No .noraa/logs directory found under the target repo.")
        candidates = sorted([p for p in logs_root.iterdir() if p.is_dir() and "verify" in p.name])
        if not candidates:
            raise SystemExit("No verify logs found.")
        ld = candidates[-1]

    code, msg, rule_id, script_text = diagnose_log(
        ld,
        repo_root,
        deps_prefix=deps_prefix,
        esmf_mkfile=esmf_mkfile,
    )
    (ld / "diagnosis.txt").write_text(msg)

    if rule_id and script_text:
        fix_path = ld / f"fix_{rule_id}.sh"
        fix_path.write_text(script_text)
        os.chmod(fix_path, 0o755)
        print(f"Generated fix script: {fix_path}")
        print(f"Run it with: bash {fix_path}")

    print(msg, end="")
    raise SystemExit(code)


@app.command()
def bootstrap(
    dest: str = typer.Option(str(Path.home() / "work" / "ufsatm"), "--dest"),
    upstream_url: str = typer.Option("https://github.com/NOAA-EMC/ufsatm.git", "--upstream-url"),
):
    """
    Create a fresh upstream ufsatm checkout (if needed) and initialize NORAA config under it.

    - If dest does not exist: clone upstream into dest.
    - If dest exists and is a git repo: do not modify it, just instruct to run noraa init.
    - After clone/init, prints the next commands for doctor + verify.
    """
    if which("git") is None:
        raise SystemExit("git not found in PATH. Install git and retry.")

    dest_path = Path(dest).expanduser().resolve()

    if dest_path.exists():
        git_dir = dest_path / ".git"
        if git_dir.exists():
            print("Destination already exists and looks like a git repo.")
            print("Run this instead:")
            print(f"  noraa init --repo {dest_path}")
            raise SystemExit(0)

        print("Destination path already exists but is not a git repo.")
        print("Choose a different --dest or remove the directory.")
        raise SystemExit(2)

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Cloning upstream ufsatm into: {dest_path}")
    rc = run_streamed(
        ["git", "clone", upstream_url, str(dest_path)],
        cwd=dest_path.parent,
        out_dir=log_dir(dest_path.parent, "bootstrap-clone"),
        env=os.environ.copy(),
    )
    if rc != 0:
        raise SystemExit(rc)

    repo_root = _target_repo(str(dest_path))
    cfg = ProjectConfig(repo_path=str(repo_root), upstream_url=upstream_url)
    write_project(repo_root, cfg)

    print("Initialized NORAA config:")
    print(f"  {repo_root / '.noraa' / 'project.toml'}")
    print("\nNext steps:")
    print(f"  noraa doctor --repo {repo_root}")
    print(
        f"  noraa verify --repo {repo_root} \\\n"
        "    --deps-prefix /path/to/deps \\\n"
        '    --esmf-mkfile "/path/to/esmf.mk"\n'
    )


def main():
    app()


if __name__ == "__main__":
    main()
