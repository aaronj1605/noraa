from __future__ import annotations

from pathlib import Path
import os
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
        env["LD_LIBRARY_PATH"] = f"{deps_prefix}/lib:{deps_prefix}/lib64:" + env.get(
            "LD_LIBRARY_PATH", ""
        )
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


def _detect_verify_script(repo_root: Path) -> Path | None:
    candidates = [
        repo_root / "scripts" / "verify_mpas_smoke.sh",
        repo_root / "scripts" / "verify.sh",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _pick_mpas_suite(repo_root: Path) -> str:
    """Discover a valid MPAS CCPP suite, preferring suite_MPAS_RRFS.xml."""
    suites_dir = repo_root / "ccpp" / "suites"
    if not suites_dir.exists():
        raise SystemExit(f"Missing ccpp/suites under {repo_root}")

    preferred = suites_dir / "suite_MPAS_RRFS.xml"
    if preferred.exists():
        return preferred.stem  # suite_MPAS_RRFS

    mpas = sorted(suites_dir.glob("suite_MPAS*.xml"))
    if mpas:
        return mpas[0].stem

    raise SystemExit(
        "No MPAS suite XML found under ccpp/suites. "
        "Expected something like suite_MPAS_RRFS.xml."
    )


def _cmake_fallback_mpas(
    repo_root: Path, out: Path, env: dict[str, str], clean: bool
) -> int:
    """Fallback: configure & build MPAS-only via CMake under .noraa/build."""
    build_dir = repo_root / ".noraa" / "build"
    if clean and build_dir.exists():
        safe_check_output(
            ["bash", "-lc", "rm -rf .noraa/build"], cwd=repo_root, env=env
        )

    suite = _pick_mpas_suite(repo_root)

    configure = [
        "cmake",
        "-S",
        str(repo_root),
        "-B",
        str(build_dir),
        "-DMPAS=ON",
        "-DFV3=OFF",
        f"-DCCPP_SUITES={suite}",
    ]

    jobs = str(max(1, (os.cpu_count() or 1)))
    build = ["cmake", "--build", str(build_dir), "-j", jobs]

    rc1 = run_streamed(configure, repo_root, out, env)
    if rc1 != 0:
        return rc1
    rc2 = run_streamed(build, repo_root, out, env)
    return rc2


def _bootstrapped_esmf_mk(repo_root: Path) -> Path | None:
    """Return esmf.mk from NORAA-managed ESMF install, if present."""
    install_root = repo_root / ".noraa" / "esmf" / "install"
    if not install_root.exists():
        return None
    for mk in install_root.rglob("esmf.mk"):
        return mk
    return None


def _resolve_esmf_mkfile(
    repo_root: Path, deps_prefix: str | None, esmf_mkfile: str | None
) -> str:
    """Resolve esmf.mk from explicit flag, bootstrapped install, or deps_prefix."""
    if esmf_mkfile:
        p = Path(esmf_mkfile)
        if p.exists():
            return str(p)

    mk = _bootstrapped_esmf_mk(repo_root)
    if mk:
        return str(mk)

    if deps_prefix:
        candidate = Path(deps_prefix) / "lib" / "esmf.mk"
        if candidate.exists():
            return str(candidate)

    raise SystemExit(
        "ESMF not found. Run: noraa bootstrap esmf --repo <ufsatm>\n"
        "or provide --esmf-mkfile / --deps-prefix"
    )


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

    resolved_esmf = _resolve_esmf_mkfile(repo_root, deps_prefix, esmf_mkfile)
    env = _build_env(deps_prefix, resolved_esmf)

    write_env_snapshot(out, env)
    write_tool_snapshot(out, env)

    script = Path(cfg.verify_script) if cfg.verify_script else None
    if not script or not script.exists():
        detected = _detect_verify_script(repo_root)
        if detected:
            script = detected

    if script and script.exists():
        if clean:
            safe_check_output(
                ["bash", "-lc", "rm -rf .noraa/build"], cwd=repo_root, env=env
            )
        rc = run_streamed(["bash", str(script)], repo_root, out, env)
    else:
        rc = _cmake_fallback_mpas(repo_root, out, env, clean)

    (out / "exit_code.txt").write_text(f"{rc}\n")

    v = validate_mpas_success(repo_root, deps_prefix, out)
    (out / "postcheck.txt").write_text(f"ok={v.ok}\nreason={v.reason}\n")

    if rc != 0 or not v.ok:
        code, msg, rule_id, script_text = diagnose_log(
            out, repo_root, deps_prefix=deps_prefix, esmf_mkfile=resolved_esmf
        )
        (out / "diagnosis.txt").write_text(msg)
        print(msg, end="")
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

    Currently supported components:
      - esmf  -> clone, build, and install ESMF into .noraa/esmf/install
    """
    repo_root = _target_repo(repo)
    _require_project(repo_root)

    if component != "esmf":
        raise SystemExit("Only supported bootstrap component is: esmf")

    out = log_dir(repo_root, "bootstrap-esmf")

    base = repo_root / ".noraa" / "esmf"
    src = base / "src"
    inst = base / "install"

    base.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    write_env_snapshot(out, env)
    write_tool_snapshot(out, env)

    # Clone ESMF if necessary
    if not src.exists():
        rc_clone = run_streamed(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                esmf_branch,
                "https://github.com/esmf-org/esmf.git",
                str(src),
            ],
            repo_root,
            out,
            env,
        )
        if rc_clone != 0:
            raise SystemExit("ESMF bootstrap failed during git clone")

    # Configure ESMF build environment. We assume compilers/MPI are already loaded.
    build_env = env.copy()
    build_env.setdefault("ESMF_COMM", "openmpi")
    build_env.setdefault("ESMF_COMPILER", "gfortran")
    build_env.setdefault("ESMF_BOPT", "O")
    build_env["ESMF_DIR"] = str(src)
    build_env["ESMF_INSTALL_PREFIX"] = str(inst)

    rc_build = run_streamed(
        ["bash", "-lc", "make install"],
        src,
        out,
        build_env,
    )

    (out / "exit_code.txt").write_text(f"{rc_build}\n")

    if rc_build != 0:
        raise SystemExit("ESMF bootstrap failed during build/install")

    mk = _bootstrapped_esmf_mk(repo_root)
    if not mk:
        raise SystemExit(
            "ESMF build completed but esmf.mk was not found under .noraa/esmf/install"
        )

    (out / "esmf_mkfile.txt").write_text(str(mk) + "\n")
    print(f"ESMF installed under {inst}")
    print(f"Detected esmf.mk at: {mk}")


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
            raise SystemExit("No .noraa/logs directory found to diagnose.")
        # Pick latest verify log by timestamped directory name.
        candidates = sorted(
            p for p in logs_root.iterdir() if p.is_dir() and p.name.endswith("-verify")
        )
        if not candidates:
            raise SystemExit("No verify logs found under .noraa/logs to diagnose.")
        log_dir_path = candidates[-1]

    code, msg, rule_id, script_text = diagnose_log(
        log_dir_path, repo_root, deps_prefix=None, esmf_mkfile=None
    )
    print(msg, end="")
    raise SystemExit(code)


def main():
    app()


if __name__ == "__main__":
    main()

