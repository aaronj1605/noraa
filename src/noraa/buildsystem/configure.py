from __future__ import annotations

from pathlib import Path

from ..util import run_streamed, safe_check_output
from .find_deps import render_find_deps_script
from .paths import pick_mpas_suite


def cmake_fallback_mpas(
    *,
    repo_root: Path,
    out: Path,
    env: dict[str, str],
    clean: bool,
    deps_prefix: str | None,
    esmf_mkfile: str | None,
    python_executable: str,
) -> int:
    build_dir = repo_root / ".noraa" / "build"
    if clean and build_dir.exists():
        safe_check_output(["bash", "-lc", "rm -rf .noraa/build"], cwd=repo_root, env=env)

    suite = pick_mpas_suite(repo_root)

    wrapper_src = repo_root / ".noraa" / "wrapper-src"
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
        f"-DPython_EXECUTABLE={python_executable}",
        f"-DPython3_EXECUTABLE={python_executable}",
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
        netcdff_lib = next(
            (p for p in (lib_dir / "libnetcdff.so", lib_dir / "libnetcdff.a") if p.exists()),
            None,
        )
        netcdf_lib = next(
            (p for p in (lib_dir / "libnetcdf.so", lib_dir / "libnetcdf.a") if p.exists()),
            None,
        )
        fms_lib = next(
            (p for p in (lib_dir / "libfms.a", lib_dir / "libfms_r4.a") if p.exists()),
            lib_dir / "libfms.a",
        )
        fms_include_dirs = [
            str(p)
            for p in (
                deps_root / "include",
                deps_root / "include" / "fms",
                deps_root / "include" / "FMS",
                deps_root / "include_r4",
            )
            if p.exists()
        ]
        fms_include = ";".join(fms_include_dirs) if fms_include_dirs else str(include_generic)

        find_deps.write_text(
            render_find_deps_script(
                repo_root=repo_root,
                lib_dir=lib_dir,
                include_4=include_4,
                include_d=include_d,
                include_generic=include_generic,
                netcdf_lib=netcdf_lib,
                netcdff_lib=netcdff_lib,
                fms_lib=fms_lib,
                fms_include=fms_include,
            )
        )
        configure.append(f"-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES={find_deps}")

    if esmf_mkfile:
        configure.append(f"-DESMFMKFILE={esmf_mkfile}")
        mk_path = Path(esmf_mkfile)
        if mk_path.exists():
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

    cache_file = build_dir / "CMakeCache.txt"
    if cache_file.exists():
        cache_text = cache_file.read_text(encoding="utf-8", errors="ignore")
        expected_home = f"CMAKE_HOME_DIRECTORY:INTERNAL={wrapper_src.resolve()}"
        if expected_home not in cache_text:
            safe_check_output(["bash", "-lc", "rm -rf .noraa/build"], cwd=repo_root, env=env)

    rc1 = run_streamed(configure, repo_root, out, env)
    if rc1 != 0:
        return rc1
    return run_streamed(["cmake", "--build", str(build_dir), "-j", "1"], repo_root, out, env)
