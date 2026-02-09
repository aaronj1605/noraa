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


def _repo_cmd(repo_root: Path, *parts: str) -> str:
    tokens = ["noraa", *parts, "--repo", str(repo_root)]
    return " ".join(tokens)


def _fail(message: str, *, next_step: str | None = None, logs: Path | None = None) -> None:
    lines = [message]
    if logs is not None:
        lines.append(f"Logs: {logs}")
    if next_step:
        lines.append(f"Next step: {next_step}")
    raise SystemExit("\n".join(lines))


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

    # Stabilize Fortran builds on clean Linux toolchains where upstream sources
    # can trigger line-length/range diagnostics as hard failures.
    fflags = env.get("FFLAGS", "")
    extra_fflags = (
        " -ffree-line-length-none"
        " -fno-range-check"
        " -Wno-error=line-truncation"
    )
    env["FFLAGS"] = (fflags + extra_fflags).strip()
    return env


def _require_project(repo_root: Path) -> ProjectConfig:
    cfg = load_project(repo_root)
    if cfg is None:
        _fail(
            "Missing .noraa/project.toml under the target repo.",
            next_step=_repo_cmd(repo_root, "init"),
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
        _fail(
            f"Missing ccpp/suites under {repo_root}",
            next_step=_repo_cmd(repo_root, "verify"),
        )

    preferred = suites_dir / "suite_MPAS_RRFS.xml"
    if preferred.exists():
        return preferred.stem  # suite_MPAS_RRFS

    mpas = sorted(suites_dir.glob("suite_MPAS*.xml"))
    if mpas:
        return mpas[0].stem

    _fail(
        "No MPAS suite XML found under ccpp/suites. "
        "Expected something like suite_MPAS_RRFS.xml.",
        next_step=_repo_cmd(repo_root, "verify"),
    )


def _cmake_fallback_mpas(
    repo_root: Path,
    out: Path,
    env: dict[str, str],
    clean: bool,
    deps_prefix: str | None,
    esmf_mkfile: str | None,
) -> int:
    """Fallback: configure & build MPAS-only via CMake under .noraa/build."""
    build_dir = repo_root / ".noraa" / "build"
    if clean and build_dir.exists():
        safe_check_output(
            ["bash", "-lc", "rm -rf .noraa/build"], cwd=repo_root, env=env
        )

    suite = _pick_mpas_suite(repo_root)

    # Build through a NORAA-owned wrapper top-level project to ensure C/CXX/
    # Fortran language state is initialized before upstream subdirs run.
    wrapper_src = out / "wrapper-src"
    wrapper_src.mkdir(parents=True, exist_ok=True)
    (wrapper_src / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.19)\n"
        "project(noraa_ufsatm_wrapper LANGUAGES C CXX Fortran)\n"
        f'add_subdirectory("{repo_root}" ufsatm)\n'
    )

    configure = [
        "cmake",
        "-S",
        str(wrapper_src),
        "-B",
        str(build_dir),
        "-DMPAS=ON",
        "-DFV3=OFF",
        f"-DCCPP_SUITES={suite}",
    ]

    module_paths: list[str] = []
    mpas_modules = repo_root / "mpas" / "MPAS-Model" / "cmake" / "Modules"
    if mpas_modules.exists():
        module_paths.append(str(mpas_modules))

    find_deps = out / "find_deps.cmake"
    if deps_prefix:
        configure.append(f"-DCMAKE_PREFIX_PATH={deps_prefix}")

        deps_root = Path(deps_prefix)
        lib_dir = deps_root / "lib"
        include_4 = deps_root / "include_4"
        include_d = deps_root / "include_d"
        include_generic = deps_root / "include"
        netcdff_candidates = [
            lib_dir / "libnetcdff.so",
            lib_dir / "libnetcdff.a",
        ]
        netcdff_lib = next((p for p in netcdff_candidates if p.exists()), None)
        netcdf_candidates = [
            lib_dir / "libnetcdf.so",
            lib_dir / "libnetcdf.a",
        ]
        netcdf_lib = next((p for p in netcdf_candidates if p.exists()), None)
        fms_lib_candidates = [lib_dir / "libfms.a", lib_dir / "libfms_r4.a"]
        fms_lib = next((p for p in fms_lib_candidates if p.exists()), fms_lib_candidates[0])
        fms_include_candidates = [
            deps_root / "include",
            deps_root / "include" / "fms",
            deps_root / "include" / "FMS",
            deps_root / "include_r4",
        ]
        fms_include_dirs = [str(p) for p in fms_include_candidates if p.exists()]
        fms_include = ";".join(fms_include_dirs) if fms_include_dirs else str(include_generic)

        find_deps.write_text(
            f"if(NOT EXISTS \"{lib_dir / 'libbacio_4.a'}\")\n"
            f"  message(FATAL_ERROR \"Missing dependency: {lib_dir / 'libbacio_4.a'}\")\n"
            "endif()\n"
            f"if(NOT EXISTS \"{lib_dir / 'libsp_d.a'}\")\n"
            f"  message(FATAL_ERROR \"Missing dependency: {lib_dir / 'libsp_d.a'}\")\n"
            "endif()\n"
            f"if(NOT EXISTS \"{lib_dir / 'libw3emc_d.a'}\")\n"
            f"  message(FATAL_ERROR \"Missing dependency: {lib_dir / 'libw3emc_d.a'}\")\n"
            "endif()\n"
            f"if(NOT EXISTS \"{lib_dir / 'libpiof.a'}\")\n"
            f"  message(FATAL_ERROR \"Missing dependency: {lib_dir / 'libpiof.a'}\")\n"
            "endif()\n"
            f"if(NOT EXISTS \"{lib_dir / 'libpioc.a'}\")\n"
            f"  message(FATAL_ERROR \"Missing dependency: {lib_dir / 'libpioc.a'}\")\n"
            "endif()\n"
            f"if(NOT EXISTS \"{fms_lib}\")\n"
            f"  message(FATAL_ERROR \"Missing dependency: {fms_lib}\")\n"
            "endif()\n"
            "if(NOT TARGET bacio::bacio_4)\n"
            "  add_library(bacio::bacio_4 STATIC IMPORTED GLOBAL)\n"
            f"  set_target_properties(bacio::bacio_4 PROPERTIES IMPORTED_LOCATION \"{lib_dir / 'libbacio_4.a'}\" INTERFACE_INCLUDE_DIRECTORIES \"{include_4}\")\n"
            "endif()\n"
            "if(NOT TARGET bufr::bufr_4)\n"
            "  add_library(bufr::bufr_4 STATIC IMPORTED GLOBAL)\n"
            f"  set_target_properties(bufr::bufr_4 PROPERTIES IMPORTED_LOCATION \"{lib_dir / 'libbufr_4.a'}\" INTERFACE_INCLUDE_DIRECTORIES \"{include_4}\")\n"
            "endif()\n"
            "if(NOT TARGET sp::sp_d)\n"
            "  add_library(sp::sp_d STATIC IMPORTED GLOBAL)\n"
            f"  set_target_properties(sp::sp_d PROPERTIES IMPORTED_LOCATION \"{lib_dir / 'libsp_d.a'}\")\n"
            "endif()\n"
            "if(NOT TARGET w3emc::w3emc_d)\n"
            "  add_library(w3emc::w3emc_d STATIC IMPORTED GLOBAL)\n"
            f"  set_target_properties(w3emc::w3emc_d PROPERTIES IMPORTED_LOCATION \"{lib_dir / 'libw3emc_d.a'}\" INTERFACE_INCLUDE_DIRECTORIES \"{include_d}\")\n"
            "  target_link_libraries(w3emc::w3emc_d INTERFACE bacio::bacio_4 bufr::bufr_4)\n"
            "endif()\n"
            "if(NOT TARGET PIO::PIO_C)\n"
            "  add_library(PIO::PIO_C STATIC IMPORTED GLOBAL)\n"
            f"  set_target_properties(PIO::PIO_C PROPERTIES IMPORTED_LOCATION \"{lib_dir / 'libpioc.a'}\" INTERFACE_INCLUDE_DIRECTORIES \"{include_generic}\")\n"
            f"  target_link_libraries(PIO::PIO_C INTERFACE {str(netcdf_lib) if netcdf_lib else 'netcdf'})\n"
            "endif()\n"
            "if(NOT TARGET PIO::PIO_Fortran)\n"
            "  add_library(PIO::PIO_Fortran STATIC IMPORTED GLOBAL)\n"
            f"  set_target_properties(PIO::PIO_Fortran PROPERTIES IMPORTED_LOCATION \"{lib_dir / 'libpiof.a'}\" INTERFACE_INCLUDE_DIRECTORIES \"{include_generic}\")\n"
            "  target_link_libraries(PIO::PIO_Fortran INTERFACE PIO::PIO_C)\n"
            "endif()\n"
            "if(NOT TARGET fms)\n"
            "  add_library(fms STATIC IMPORTED GLOBAL)\n"
            f"  set_target_properties(fms PROPERTIES IMPORTED_LOCATION \"{fms_lib}\" INTERFACE_INCLUDE_DIRECTORIES \"{fms_include}\")\n"
            "endif()\n"
            "if(NOT TARGET NetCDF::NetCDF_Fortran)\n"
            "  add_library(NetCDF::NetCDF_Fortran INTERFACE IMPORTED GLOBAL)\n"
            f"  set_target_properties(NetCDF::NetCDF_Fortran PROPERTIES INTERFACE_INCLUDE_DIRECTORIES \"{include_generic};/usr/include\")\n"
            f"  target_link_libraries(NetCDF::NetCDF_Fortran INTERFACE {str(netcdff_lib) if netcdff_lib else 'netcdff'} {str(netcdf_lib) if netcdf_lib else 'netcdf'})\n"
            "endif()\n"
            "if(NOT TARGET PnetCDF::PnetCDF_Fortran)\n"
            "  add_library(PnetCDF::PnetCDF_Fortran INTERFACE IMPORTED GLOBAL)\n"
            "  target_link_libraries(PnetCDF::PnetCDF_Fortran INTERFACE pnetcdf)\n"
            "endif()\n"
            "if(NOT TARGET MPI::MPI_Fortran)\n"
            "  add_library(MPI::MPI_Fortran INTERFACE IMPORTED GLOBAL)\n"
            "  target_link_libraries(MPI::MPI_Fortran INTERFACE mpi_usempif08 mpi_usempi_ignore_tkr mpi_mpifh mpi)\n"
            "endif()\n"
            f"set_source_files_properties(\"{repo_root / 'ccpp/physics/physics/hooks/machine.F'}\" PROPERTIES Fortran_FORMAT FREE COMPILE_FLAGS \"-ffree-form\")\n"
        )
        configure.append(f"-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES={find_deps}")

    if esmf_mkfile:
        configure.append(f"-DESMFMKFILE={esmf_mkfile}")

        mk_path = Path(esmf_mkfile)
        if mk_path.exists():
            # Bootstrapped ESMF installs FindESMF.cmake under include/ESMX/.../cmake.
            install_root = mk_path.parent.parent.parent.parent
            candidates = [
                install_root / "include" / "ESMX" / "Driver" / "cmake",
                install_root / "include" / "ESMX" / "Comps" / "ESMX_Data" / "cmake",
            ]
            module_paths.extend(str(c) for c in candidates if c.exists())

            if deps_prefix and find_deps.exists():
                esmf_lib = mk_path.parent / "libesmf.so"
                if esmf_lib.exists():
                    with find_deps.open("a", encoding="utf-8") as f:
                        f.write(
                            "if(NOT TARGET ESMF::ESMF)\n"
                            "  add_library(ESMF::ESMF SHARED IMPORTED GLOBAL)\n"
                            f"  set_target_properties(ESMF::ESMF PROPERTIES IMPORTED_LOCATION \"{esmf_lib}\")\n"
                            "endif()\n"
                        )

    if module_paths:
        configure.append(f"-DCMAKE_MODULE_PATH={';'.join(module_paths)}")

    # Deterministic order avoids intermittent Fortran module races in MPAS build.
    build = ["cmake", "--build", str(build_dir), "-j", "1"]

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


def _bootstrapped_deps_prefix(repo_root: Path) -> Path | None:
    """Return NORAA-managed deps install prefix, if present."""
    install_root = repo_root / ".noraa" / "deps" / "install"
    if install_root.exists():
        return install_root
    return None


def _resolve_deps_prefix(repo_root: Path, deps_prefix: str | None) -> str | None:
    """Resolve deps prefix from explicit flag or NORAA-managed bootstrap."""
    if deps_prefix:
        p = Path(deps_prefix)
        if p.exists():
            return str(p)

    p = _bootstrapped_deps_prefix(repo_root)
    if p:
        return str(p)

    return None


def _clone_with_retries(
    repo_root: Path, out: Path, env: dict[str, str], url: str, branch: str, dest: Path
) -> int:
    attempts = 3
    for i in range(1, attempts + 1):
        rc = run_streamed(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                branch,
                url,
                str(dest),
            ],
            repo_root,
            out,
            env,
        )
        if rc == 0:
            return 0

        safe_check_output(["rm", "-rf", str(dest)], cwd=repo_root, env=env)
        if i < attempts:
            print(f"Retrying clone ({i + 1}/{attempts}) for {dest.name}...")

    return 1


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

    _fail(
        "ESMF not found under .noraa/esmf/install and no valid --esmf-mkfile was provided.",
        next_step=_repo_cmd(repo_root, "bootstrap", "esmf"),
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

    resolved_deps = _resolve_deps_prefix(repo_root, deps_prefix)
    resolved_esmf = _resolve_esmf_mkfile(repo_root, resolved_deps, esmf_mkfile)
    env = _build_env(resolved_deps, resolved_esmf)

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
        rc = _cmake_fallback_mpas(
            repo_root,
            out,
            env,
            clean,
            deps_prefix=resolved_deps,
            esmf_mkfile=resolved_esmf,
        )

    (out / "exit_code.txt").write_text(f"{rc}\n")

    v = validate_mpas_success(repo_root, resolved_deps, out)
    (out / "postcheck.txt").write_text(f"ok={v.ok}\nreason={v.reason}\n")

    if rc != 0 or not v.ok:
        code, msg, rule_id, script_text = diagnose_log(
            out, repo_root, deps_prefix=resolved_deps, esmf_mkfile=resolved_esmf
        )
        (out / "diagnosis.txt").write_text(msg)
        print(msg, end="")
        print(
            f"\nNext step: noraa diagnose --repo {repo_root} --log-dir {out}",
            end="",
        )
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
        out = log_dir(repo_root, "bootstrap-esmf")

        base = repo_root / ".noraa" / "esmf"
        src = base / "src"
        inst = base / "install"

        base.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        write_env_snapshot(out, env)
        write_tool_snapshot(out, env)

        if not src.exists():
            rc_clone = _clone_with_retries(
                repo_root,
                out,
                env,
                "https://github.com/esmf-org/esmf.git",
                esmf_branch,
                src,
            )
            if rc_clone != 0:
                _fail(
                    "ESMF bootstrap failed during git clone.",
                    logs=out,
                    next_step=_repo_cmd(repo_root, "bootstrap", "esmf"),
                )

        build_env = env.copy()
        build_env.setdefault("ESMF_COMM", "openmpi")
        build_env.setdefault("ESMF_COMPILER", "gfortran")
        build_env.setdefault("ESMF_BOPT", "O")
        build_env["ESMF_DIR"] = str(src)
        build_env["ESMF_INSTALL_PREFIX"] = str(inst)

        jobs = str(max(1, (os.cpu_count() or 1)))
        rc_build = run_streamed(["make", "-j", jobs], src, out, build_env)
        if rc_build != 0:
            (out / "exit_code.txt").write_text(f"{rc_build}\n")
            _fail(
                "ESMF bootstrap failed during build.",
                logs=out,
                next_step=_repo_cmd(repo_root, "bootstrap", "esmf"),
            )

        rc_install = run_streamed(["make", "install"], src, out, build_env)
        (out / "exit_code.txt").write_text(f"{rc_install}\n")
        if rc_install != 0:
            _fail(
                "ESMF bootstrap failed during install.",
                logs=out,
                next_step=_repo_cmd(repo_root, "bootstrap", "esmf"),
            )

        mk = _bootstrapped_esmf_mk(repo_root)
        if not mk:
            _fail(
                "ESMF build completed but esmf.mk was not found under .noraa/esmf/install.",
                logs=out,
                next_step=_repo_cmd(repo_root, "bootstrap", "esmf"),
            )

        (out / "esmf_mkfile.txt").write_text(str(mk) + "\n")
        print(f"ESMF installed under {inst}")
        print(f"Detected esmf.mk at: {mk}")
        return

    if component == "deps":
        out = log_dir(repo_root, "bootstrap-deps")

        base = repo_root / ".noraa" / "deps"
        src_root = base / "src"
        build_root = base / "build"
        inst = base / "install"

        src_root.mkdir(parents=True, exist_ok=True)
        build_root.mkdir(parents=True, exist_ok=True)
        inst.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        write_env_snapshot(out, env)
        write_tool_snapshot(out, env)

        build_env = env.copy()
        build_env.setdefault("CC", "mpicc")
        build_env.setdefault("CXX", "mpicxx")
        build_env.setdefault("FC", "mpifort")

        repos: list[tuple[str, str, str]] = [
            ("bacio", "https://github.com/NOAA-EMC/NCEPLIBS-bacio.git", "v2.4.1"),
            ("bufr", "https://github.com/NOAA-EMC/NCEPLIBS-bufr.git", "bufr_v12.0.0"),
            ("sp", "https://github.com/NOAA-EMC/NCEPLIBS-sp.git", "v2.5.0"),
            ("w3emc", "https://github.com/NOAA-EMC/NCEPLIBS-w3emc.git", "v2.10.0"),
            ("netcdf-fortran", "https://github.com/Unidata/netcdf-fortran.git", "v4.6.1"),
            ("pio", "https://github.com/NCAR/ParallelIO.git", "pio2_6_9"),
            ("fms", "https://github.com/NOAA-GFDL/FMS.git", "2025.04"),
        ]

        jobs = str(max(1, (os.cpu_count() or 1)))

        for name, url, tag in repos:
            src = src_root / name
            bld = build_root / name

            if not src.exists():
                rc = _clone_with_retries(repo_root, out, build_env, url, tag, src)
                if rc != 0:
                    (out / "exit_code.txt").write_text("1\n")
                    _fail(
                        f"Dependency bootstrap failed while cloning {name}.",
                        logs=out,
                        next_step=_repo_cmd(repo_root, "bootstrap", "deps"),
                    )

            extra = [
                f"-DCMAKE_INSTALL_PREFIX={inst}",
                f"-DCMAKE_PREFIX_PATH={inst}",
            ]
            if name == "pio":
                extra.extend(
                    [
                        "-DPIO_ENABLE_TESTS=OFF",
                        "-DPIO_ENABLE_EXAMPLES=OFF",
                        "-DPIO_ENABLE_TIMING=OFF",
                        "-DPIO_ENABLE_FORTRAN=ON",
                    ]
                )
            if name == "bufr":
                extra.append("-DBUILD_TESTING=OFF")
            if name == "netcdf-fortran":
                extra.extend(
                    [
                        "-DBUILD_TESTING=OFF",
                        "-DNETCDF_C_INCLUDE_DIR=/usr/include",
                        "-DNETCDF_C_LIBRARY=/usr/lib/x86_64-linux-gnu/libnetcdf.so",
                    ]
                )

            rc_cfg = run_streamed(
                ["cmake", "-S", str(src), "-B", str(bld), *extra],
                repo_root,
                out,
                build_env,
            )
            if rc_cfg != 0:
                (out / "exit_code.txt").write_text(f"{rc_cfg}\n")
                _fail(
                    f"Dependency bootstrap failed while configuring {name}.",
                    logs=out,
                    next_step=_repo_cmd(repo_root, "bootstrap", "deps"),
                )

            rc_build = run_streamed(
                ["cmake", "--build", str(bld), "-j", jobs],
                repo_root,
                out,
                build_env,
            )
            if rc_build != 0:
                (out / "exit_code.txt").write_text(f"{rc_build}\n")
                _fail(
                    f"Dependency bootstrap failed while building {name}.",
                    logs=out,
                    next_step=_repo_cmd(repo_root, "bootstrap", "deps"),
                )

            rc_install = run_streamed(["cmake", "--install", str(bld)], repo_root, out, build_env)
            if rc_install != 0:
                (out / "exit_code.txt").write_text(f"{rc_install}\n")
                _fail(
                    f"Dependency bootstrap failed while installing {name}.",
                    logs=out,
                    next_step=_repo_cmd(repo_root, "bootstrap", "deps"),
                )

        required = [
            inst / "lib" / "cmake" / "bacio" / "bacio-config.cmake",
            inst / "lib" / "cmake" / "bufr" / "bufr-config.cmake",
            inst / "lib" / "cmake" / "sp" / "sp-config.cmake",
            inst / "lib" / "cmake" / "w3emc" / "w3emc-config.cmake",
            inst / "lib" / "cmake" / "PIO" / "PIOConfig.cmake",
        ]
        fms_candidates = [inst / "lib" / "libfms.a", inst / "lib" / "libfms_r4.a"]
        missing = [str(x) for x in required if not x.exists()]
        netcdff_candidates = [inst / "lib" / "libnetcdff.so", inst / "lib" / "libnetcdff.a"]
        if not any(p.exists() for p in netcdff_candidates):
            missing.append(str(netcdff_candidates[0]))
        if not any(p.exists() for p in fms_candidates):
            missing.append(str(fms_candidates[0]))
        if missing:
            (out / "exit_code.txt").write_text("1\n")
            (out / "missing_deps.txt").write_text("\n".join(missing) + "\n")
            _fail(
                "Dependency bootstrap completed with missing package config files.",
                logs=out,
                next_step=_repo_cmd(repo_root, "bootstrap", "deps"),
            )

        (out / "exit_code.txt").write_text("0\n")
        (out / "deps_prefix.txt").write_text(str(inst) + "\n")
        print(f"Dependencies installed under {inst}")
        return

    _fail(
        f"Unsupported bootstrap component: {component}",
        next_step=f"{_repo_cmd(repo_root, 'bootstrap', 'deps')}  or  {_repo_cmd(repo_root, 'bootstrap', 'esmf')}",
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
            _fail(
                "No .noraa/logs directory found to diagnose.",
                next_step=_repo_cmd(repo_root, "verify"),
            )
        candidates = sorted(
            p for p in logs_root.iterdir() if p.is_dir() and p.name.endswith("-verify")
        )
        if not candidates:
            _fail(
                "No verify logs found under .noraa/logs to diagnose.",
                next_step=_repo_cmd(repo_root, "verify"),
            )
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
