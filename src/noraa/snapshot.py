from __future__ import annotations

from pathlib import Path
import json
from .util import which, safe_check_output

INTERESTING_ENV_KEYS = [
    "DEPS_PREFIX",
    "ESMFMKFILE",
    "PATH",
    "LD_LIBRARY_PATH",
    "CMAKE_PREFIX_PATH",
    "CMAKE_MODULE_PATH",
]


def write_env_snapshot(out_dir: Path, env: dict[str, str]) -> None:
    snap = {k: env.get(k, "") for k in INTERESTING_ENV_KEYS}
    (out_dir / "env.json").write_text(json.dumps(snap, indent=2) + "\n")


def write_tool_snapshot(out_dir: Path, env: dict[str, str]) -> None:
    tools = {
        "git": which("git"),
        "python": which("python3", "python"),
        "cmake": which("cmake"),
        "make": which("make", "gmake"),
        "mpicc": which("mpicc"),
        "mpifort": which("mpifort"),
        "mpiexec": which("mpiexec", "mpirun"),
    }
    lines = []

    # Realpaths for MPI wrappers (helps detect symlink overlays)
    mpiexec_real = ""
    mpi_prefix = ""
    try:
        if tools.get("mpiexec"):
            mpiexec_real = str(Path(tools["mpiexec"]).resolve())
            mpi_prefix = str(Path(mpiexec_real).parent.parent)
    except Exception:
        pass
    ok = True
    for k, v in tools.items():
        if v:
            lines.append(f"[ok] {k}: {v}")
        else:
            ok = False
            lines.append(f"[missing] {k}")

    if mpiexec_real:
        lines.append(f"[info] mpiexec_real: {mpiexec_real}")
    if mpi_prefix:
        lines.append(f"[info] mpi_prefix: {mpi_prefix}")

    (out_dir / "tools.txt").write_text("\n".join(lines) + "\n")
    (out_dir / "mpi_prefix.txt").write_text(
        f"mpiexec={tools.get('mpiexec', '')}\n"
        f"mpiexec_real={mpiexec_real}\n"
        f"mpi_prefix={mpi_prefix}\n"
    )
    (out_dir / "mpiexec_version.txt").write_text(safe_check_output(["mpiexec", "--version"], env=env))
    (out_dir / "mpicc_show.txt").write_text(safe_check_output(["mpicc", "-show"], env=env))
    (out_dir / "mpifort_show.txt").write_text(safe_check_output(["mpifort", "-show"], env=env))
    (out_dir / "snapshot_status.txt").write_text("ok\n" if ok else "missing_tools\n")
