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


def _pick_mpas_suite(repo_root: Path) -> str:
    suites_dir = repo_root / "ccpp" / "suites"
    if not suites_dir.exists():
        raise SystemExit(f"Missing ccpp/suites under {repo_root}")

    # Prefer the suite you already confirmed exists on develop
    preferred = suites_dir / "suite_MPAS_RRFS.xml"
    if preferred.exists():
        return preferred.stem  # suite_MPAS_RRFS

    # Otherwise, pick the first MPAS suite present
    mpas = sorted(suites_dir.glob("suite_MPAS*.xml"))
    if mpas:
        return mpas[0].stem

    raise SystemExit(
        "No MPAS suite XML found under ccpp/suites. "
        "Expected something like suite_MPAS_RRFS.xml."
    )


def _cmake_fallback_mpas(repo_root: Path, out: Path, env: dict[str, str], clean: bool) -> int:
    build_dir = repo_root / ".noraa" / "build"
    if clean and build_dir.exists():
        safe_check_output(["bash", "-lc", "rm -rf .noraa/build"], cwd=repo_root, env=env)

    suite = _pick_mpas_suite(repo_root)

    esmf_mk = _detect_esmf_mkfile(repo_root, env)
    if esmf_mk:
        env = env.copy()
        env["ESMFMKFILE"] = esmf_mk
        (out / "esmf_detected.txt").write_text(esmf_mk + "\n")
    else:
        (out / "esmf_detected.txt").write_text("NOT FOUND\n")

    configure = [
        "cmake",
        "-S", str(repo_root),
        "-B", str(build_dir),
        "-DMPAS=ON",
        "-DFV3=OFF",
        f"-DCCPP_SUITES={suite}",
    ]

    jobs = str(max(1, (os.cpu_count() or 1)))
    build = ["cmake", "--build", str(build_dir), "-j", jobs]

    (out / "command.txt").write_text(
        "CONFIGURE:\n" + " ".join(configure) + "\n\nBUILD:\n" + " ".join(build) + "\n"
    )

    rc1 = run_streamed(configure, repo_root, out, env)
    if rc1 != 0:
        return rc1
    rc2 = run_streamed(build, repo_root, out, env)
    return rc2


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
    esmf_mkfile = _require_esmf(repo_root, deps_prefix, esmf_mkfile)
    env = _build_env(deps_prefix, esmf_mkfile)

    _require_esmf(deps_prefix, esmf_mkfile)

def _require_esmf(deps_prefix: str | None, esmf_mkfile: str | None):
    if esmf_mkfile and Path(esmf_mkfile).exists():
        return
    if deps_prefix:
        mk = Path(deps_prefix) / "lib" / "esmf.mk"
        if mk.exists():
            return
    raise SystemExit(
        "ESMF not found. Run: noraa bootstrap esmf --repo <ufsatm>\n"
        "or provide --esmf-mkfile / --deps-prefix"
    )


    write_env_snapshot(out, env)
    write_tool_snapshot(out, env)

    script = Path(cfg.verify_script) if cfg.verify_script else None
    if not script or not script.exists():
        detected = _detect_verify_script(repo_root)
        if detected:
            script = detected

    if script and script.exists():
        if clean:
            safe_check_output(["bash", "-lc", "rm -rf .noraa/build"], cwd=repo_root, env=env)
        rc = run_streamed(["bash", str(script)], repo_root, out, env)
    else:
        rc = _cmake_fallback_mpas(repo_root, out, env, clean)

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


@app.command()
def bootstrap(
    repo: str = typer.Option(".", "--repo"),
    component: str = typer.Argument(...),
):
    repo_root = _target_repo(repo)
    _require_project(repo_root)

    if component != "esmf":
        raise SystemExit("Only supported bootstrap component is: esmf")

    out = log_dir(repo_root, "bootstrap-esmf")
    build_dir = repo_root / ".noraa" / "esmf"
    src_dir = build_dir / "src"
    inst_dir = build_dir / "install"

    build_dir.mkdir(parents=True, exist_ok=True)

    if not src_dir.exists():
        run_streamed(
            ["git", "clone", "--depth", "1", "--branch", "v8.6.1",
             "https://github.com/esmf-org/esmf.git", str(src_dir)],
            repo_root, out, None
        )

    env = os.environ.copy()
    env["ESMF_DIR"] = str(inst_dir)
    env["ESMF_INSTALL_PREFIX"] = str(inst_dir)
    env["ESMF_COMM"] = "openmpi"
    env["ESMF_COMPILER"] = "gfortran"
    env["ESMF_NETCDF"] = "nc-config"

    rc = run_streamed(
        ["bash", "-lc", "make install"],
        src_dir, out, env
    )

    if rc != 0:
        raise SystemExit("ESMF bootstrap failed")

    print(f"ESMF installed under {inst_dir}")


def _bootstrapped_esmf_mk(repo_root: Path) -> Path | None:
    mk = repo_root / ".noraa" / "esmf" / "install" / "lib" / "esmf.mk"
    return mk if mk.exists() else None


def _require_esmf(repo_root: Path, deps_prefix: str | None, esmf_mkfile: str | None) -> str:
    if esmf_mkfile and Path(esmf_mkfile).exists():
        return esmf_mkfile

    mk = _bootstrapped_esmf_mk(repo_root)
    if mk:
        return str(mk)

    if deps_prefix:
        p = Path(deps_prefix) / "lib" / "esmf.mk"
        if p.exists():
            return str(p)

    raise SystemExit(
        "ESMF not found. Run: noraa bootstrap esmf --repo <ufsatm>"
    )


@app.command()
def bootstrap(
    repo: str = typer.Option(".", "--repo"),
    component: str = typer.Argument(...),
):
    repo_root = _target_repo(repo)
    _require_project(repo_root)

    if component != "esmf":
        raise SystemExit("Only supported bootstrap component is: esmf")

    out = log_dir(repo_root, "bootstrap-esmf")
    base = repo_root / ".noraa" / "esmf"
    src = base / "src"
    inst = base / "install"

    base.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        run_streamed(
            ["git", "clone", "--depth", "1", "--branch", "v8.6.1",
             "https://github.com/esmf-org/esmf.git", str(src)],
            repo_root, out, None
        )

    env = os.environ.copy()
    env.update({
        "ESMF_DIR": str(inst),
        "ESMF_INSTALL_PREFIX": str(inst),
        "ESMF_COMM": "openmpi",
        "ESMF_COMPILER": "gfortran",
        "ESMF_BOPT": "O",
    })

    rc = run_streamed(
        ["bash", "-lc", "make install"],
        src, out, env
    )

    if rc != 0:
        raise SystemExit("ESMF bootstrap failed")

    print(f"ESMF installed under {inst}")
