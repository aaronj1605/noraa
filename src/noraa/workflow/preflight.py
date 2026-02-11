from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from ..buildsystem.paths import bootstrapped_esmf_mk
from ..messages import repo_cmd


def cmake_version() -> tuple[int, int, int] | None:
    try:
        out = subprocess.check_output(["cmake", "--version"], text=True)
    except Exception:
        return None
    first = out.splitlines()[0] if out else ""
    marker = "version "
    if marker not in first:
        return None
    v = first.split(marker, 1)[1].strip().split()[0]
    parts = v.split(".")
    if len(parts) < 2:
        return None
    major = int(parts[0])
    minor = int(parts[1])
    patch = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    return (major, minor, patch)


def verify_preflight_issues(
    repo_root: Path,
    *,
    deps_prefix: str | None,
    esmf_mkfile: str | None,
    using_verify_script: bool,
    cmake_version_fn=cmake_version,
) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []

    ccpp_prebuild = repo_root / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    if not ccpp_prebuild.exists():
        issues.append(
            (
                f"Issue identified: Required CCPP submodule content is missing: {ccpp_prebuild}",
                "git submodule update --init --recursive",
            )
        )

    explicit_mk = Path(esmf_mkfile) if esmf_mkfile else None
    deps_mk = Path(deps_prefix) / "lib" / "esmf.mk" if deps_prefix else None
    if not (
        (explicit_mk and explicit_mk.exists())
        or bootstrapped_esmf_mk(repo_root)
        or (deps_mk and deps_mk.exists())
    ):
        issues.append(
            (
                "Issue identified: ESMF not found (missing esmf.mk under .noraa/esmf/install and no valid --esmf-mkfile/--deps-prefix).",
                repo_cmd(repo_root, "bootstrap", "esmf"),
            )
        )

    if using_verify_script:
        return issues

    deps_root = Path(deps_prefix) if deps_prefix else repo_root / ".noraa" / "deps" / "install"
    if not deps_root.exists():
        issues.append(
            (
                f"Issue identified: MPAS dependency bundle not found: {deps_root}",
                repo_cmd(repo_root, "bootstrap", "deps"),
            )
        )

    if shutil.which("pnetcdf-config") is None:
        issues.append(
            (
                "Issue identified: pnetcdf-config not found (needed for noraa bootstrap deps on clean systems).",
                "sudo apt install -y pnetcdf-bin",
            )
        )

    version = cmake_version_fn()
    if version is None:
        issues.append(
            (
                "Issue identified: CMake is required for verify fallback but was not found in PATH.",
                "pip install -U 'cmake>=3.28'",
            )
        )
    elif version < (3, 28, 0):
        issues.append(
            (
                f"Issue identified: CMake >= 3.28 is required for verify fallback (found {version[0]}.{version[1]}.{version[2]}).",
                "pip install -U 'cmake>=3.28'",
            )
        )
    return issues


def verify_preflight_failure(
    repo_root: Path,
    *,
    deps_prefix: str | None,
    esmf_mkfile: str | None,
    using_verify_script: bool,
) -> tuple[str, str] | None:
    issues = verify_preflight_issues(
        repo_root,
        deps_prefix=deps_prefix,
        esmf_mkfile=esmf_mkfile,
        using_verify_script=using_verify_script,
    )
    if not issues:
        return None
    return issues[0]


def format_preflight_summary(issues: list[tuple[str, str]]) -> str:
    lines = ["Preflight identified blocking issues:"]
    for issue, action in issues:
        lines.append(issue)
        lines.append(f"Action required: {action}")
    return "\n".join(lines)


def python_runtime_error() -> str | None:
    if sys.version_info >= (3, 11):
        return None
    return (
        "Python 3.11+ is required for noraa. "
        "On Ubuntu 22.04, install python3.11 and python3.11-venv, "
        "recreate your virtual environment, and reinstall noraa."
    )
