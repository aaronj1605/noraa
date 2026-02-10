from __future__ import annotations

import os


def build_env(deps_prefix: str | None, esmf_mkfile: str | None) -> dict[str, str]:
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

