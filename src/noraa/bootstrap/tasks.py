from __future__ import annotations

import os
from pathlib import Path

from ..messages import fail, repo_cmd
from ..snapshot import write_env_snapshot, write_tool_snapshot
from ..util import log_dir, run_streamed, safe_check_output
from ..buildsystem.paths import bootstrapped_esmf_mk


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


def bootstrap_esmf(repo_root: Path, esmf_branch: str) -> None:
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
            repo_root, out, env, "https://github.com/esmf-org/esmf.git", esmf_branch, src
        )
        if rc_clone != 0:
            fail(
                "ESMF bootstrap failed during git clone.",
                logs=out,
                next_step=repo_cmd(repo_root, "bootstrap", "esmf"),
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
        fail(
            "ESMF bootstrap failed during build.",
            logs=out,
            next_step=repo_cmd(repo_root, "bootstrap", "esmf"),
        )

    rc_install = run_streamed(["make", "install"], src, out, build_env)
    (out / "exit_code.txt").write_text(f"{rc_install}\n")
    if rc_install != 0:
        fail(
            "ESMF bootstrap failed during install.",
            logs=out,
            next_step=repo_cmd(repo_root, "bootstrap", "esmf"),
        )

    mk = bootstrapped_esmf_mk(repo_root)
    if not mk:
        fail(
            "ESMF build completed but esmf.mk was not found under .noraa/esmf/install.",
            logs=out,
            next_step=repo_cmd(repo_root, "bootstrap", "esmf"),
        )

    (out / "esmf_mkfile.txt").write_text(str(mk) + "\n")
    print(f"ESMF installed under {inst}")
    print(f"Detected esmf.mk at: {mk}")
    print(f"Next step: {repo_cmd(repo_root, "bootstrap", "deps")}")
    print(f"Then run: {repo_cmd(repo_root, "verify")}")


def bootstrap_deps(repo_root: Path) -> None:
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
                fail(
                    f"Dependency bootstrap failed while cloning {name}.",
                    logs=out,
                    next_step=repo_cmd(repo_root, "bootstrap", "deps"),
                )

        extra = [f"-DCMAKE_INSTALL_PREFIX={inst}", f"-DCMAKE_PREFIX_PATH={inst}"]
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

        rc_cfg = run_streamed(["cmake", "-S", str(src), "-B", str(bld), *extra], repo_root, out, build_env)
        if rc_cfg != 0:
            (out / "exit_code.txt").write_text(f"{rc_cfg}\n")
            fail(
                f"Dependency bootstrap failed while configuring {name}.",
                logs=out,
                next_step=repo_cmd(repo_root, "bootstrap", "deps"),
            )

        rc_build = run_streamed(["cmake", "--build", str(bld), "-j", jobs], repo_root, out, build_env)
        if rc_build != 0:
            (out / "exit_code.txt").write_text(f"{rc_build}\n")
            fail(
                f"Dependency bootstrap failed while building {name}.",
                logs=out,
                next_step=repo_cmd(repo_root, "bootstrap", "deps"),
            )

        rc_install = run_streamed(["cmake", "--install", str(bld)], repo_root, out, build_env)
        if rc_install != 0:
            (out / "exit_code.txt").write_text(f"{rc_install}\n")
            fail(
                f"Dependency bootstrap failed while installing {name}.",
                logs=out,
                next_step=repo_cmd(repo_root, "bootstrap", "deps"),
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
        fail(
            "Dependency bootstrap completed with missing package config files.",
            logs=out,
            next_step=repo_cmd(repo_root, "bootstrap", "deps"),
        )

    (out / "exit_code.txt").write_text("0\n")
    (out / "deps_prefix.txt").write_text(str(inst) + "\n")
    print(f"Dependencies installed under {inst}")

