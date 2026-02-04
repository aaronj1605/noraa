from __future__ import annotations

from pathlib import Path
import os
import typer

from .util import git_root, log_dir, run_streamed, safe_check_output
from .snapshot import write_env_snapshot, write_tool_snapshot
from .validate import validate_mpas_success
from .agent.diagnose import diagnose_log

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


@app.command()
def doctor(repo: str = typer.Option(".", "--repo")):
    repo_root = _target_repo(repo)
    out = log_dir(repo_root, "doctor")
    env = os.environ.copy()
    write_env_snapshot(out, env)
    write_tool_snapshot(out, env)
    print((out / "tools.txt").read_text(), end="")
    print(f"Logs: {out}")
    status = (out / "snapshot_status.txt").read_text().strip()
    raise SystemExit(0 if status == "ok" else 2)


@app.command()
def init(repo: str = typer.Option(".", "--repo"), force: bool = False):
    repo_root = _target_repo(repo)
    cfg = repo_root / ".noraa" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if cfg.exists() and not force:
        print(f"Config already exists: {cfg}")
        print("Use --force to overwrite.")
        raise SystemExit(0)
    cfg.write_text('version = 1\n[verify]\nscript = "scripts/verify_mpas_smoke.sh"\n')
    print(f"Wrote {cfg}")


@app.command()
def verify(
    repo: str = typer.Option(".", "--repo"),
    deps_prefix: str = typer.Option(None, "--deps-prefix"),
    esmf_mkfile: str = typer.Option(None, "--esmf-mkfile"),
    clean: bool = typer.Option(True, "--clean/--no-clean"),
):
    repo_root = _target_repo(repo)
    out = log_dir(repo_root, "verify")
    env = _build_env(deps_prefix, esmf_mkfile)

    write_env_snapshot(out, env)
    write_tool_snapshot(out, env)

    script = repo_root / "scripts" / "verify_mpas_smoke.sh"
    if not script.exists():
        raise SystemExit(f"Missing verify script: {script}")

    if clean:
        safe_check_output(["bash", "-lc", "rm -rf .noraa/build"], cwd=repo_root, env=env)

    rc = run_streamed(["bash", str(script)], repo_root, out, env)
    (out / "exit_code.txt").write_text(f"{rc}\n")

    v = validate_mpas_success(repo_root, deps_prefix, out)
    (out / "postcheck.txt").write_text(f"ok={v.ok}\nreason={v.reason}\n")

    if rc != 0 or not v.ok:
        code, msg = diagnose_log(out, repo_root, deps_prefix=deps_prefix)
        (out / "diagnosis.txt").write_text(msg)
        print(f"VERIFY FAILED. Logs: {out}")
        print(msg, end="")
        raise SystemExit(code)

    print(f"VERIFY PASSED. Logs: {out}")


@app.command()
def diagnose(
    repo: str = typer.Option(".", "--repo"),
    log_dir_path: str = typer.Option(None, "--log-dir"),
    deps_prefix: str = typer.Option(None, "--deps-prefix"),
):
    repo_root = _target_repo(repo)

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

    code, msg = diagnose_log(ld, repo_root, deps_prefix=deps_prefix)
    print(msg, end="")
    raise SystemExit(code)


def main():
    app()


if __name__ == "__main__":
    main()
