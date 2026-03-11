from __future__ import annotations

from pathlib import Path
import re
from .util import safe_check_output


class VerifyResult:
    def __init__(self, ok: bool, reason: str, executable: Path | None = None):
        self.ok = ok
        self.reason = reason
        self.executable = executable


def _extract_ldd_path(ldd_text: str, libname: str) -> str | None:
    # Matches:
    # libmpi.so.40 => /path/to/libmpi.so.40 (0x....)
    # libmpi.so.40 => not found
    pat = re.compile(rf"^\s*{re.escape(libname)}\s*=>\s*(\S+)", re.MULTILINE)
    m = pat.search(ldd_text)
    if not m:
        return None
    return m.group(1)


def _is_system_path(p: str) -> bool:
    # Conservative: treat common system lib locations as "system"
    system_prefixes = (
        "/lib/",
        "/lib64/",
        "/usr/lib/",
        "/usr/lib64/",
        "/usr/lib/x86_64-linux-gnu/",
        "/usr/local/lib/",
    )
    return any(p.startswith(pref) for pref in system_prefixes)


def _core_executable(repo_root: Path, core: str) -> Path | None:
    bin_root = repo_root / ".noraa" / "build" / "bin"
    if core == "fv3":
        candidates = [bin_root / "ufs_model", bin_root / "fv3_model"]
    else:
        candidates = [bin_root / "mpas_atmosphere"]
    for exe in candidates:
        if exe.exists():
            return exe
    return None


def validate_core_success(
    repo_root: Path, deps_prefix: str | None, out_dir: Path, core: str
) -> VerifyResult:
    exe = _core_executable(repo_root, core)
    label = "FV3 executable" if core == "fv3" else "mpas_atmosphere"
    if exe is None:
        if core == "fv3":
            stdout_path = out_dir / "stdout.txt"
            if stdout_path.exists():
                stdout = stdout_path.read_text(encoding="utf-8", errors="ignore")
                if "Built target ufsatm_fv3" in stdout:
                    return VerifyResult(True, "ok (built target ufsatm_fv3)", None)
        expected = (
            repo_root / ".noraa" / "build" / "bin" / ("ufs_model (or fv3_model)" if core == "fv3" else "mpas_atmosphere")
        )
        return VerifyResult(False, f"{label} missing at {expected}", None)

    if not exe.is_file() or not (exe.stat().st_mode & 0o111):
        return VerifyResult(False, f"{label} not executable: {exe}", exe)

    ldd = safe_check_output(["ldd", str(exe)])
    (out_dir / f"ldd_{exe.name}.txt").write_text(ldd)

    def _rp(p: str) -> str:
        try:
            return str(Path(p).resolve())
        except Exception:
            return p

    mpiexec_real = ""
    mpi_prefix = ""
    try:
        import shutil
        m = shutil.which("mpiexec") or shutil.which("mpirun")
        if m:
            mpiexec_real = _rp(m)
            mpi_prefix = str(Path(mpiexec_real).resolve().parent.parent)
    except Exception:
        pass

    # If user provided deps_prefix, validate that MPI is resolvable at runtime.
    # Do not fail just because MPI comes from system paths; many Linux setups
    # intentionally use distro OpenMPI packages.
    if deps_prefix:
        libmpi_path = _extract_ldd_path(ldd, "libmpi.so.40") or _extract_ldd_path(ldd, "libmpi.so")
        if libmpi_path is None:
            # If we cannot find libmpi, keep it a failure because runtime MPI is unknown.
            return VerifyResult(False, "Could not determine libmpi path from ldd output", exe)

        if libmpi_path == "not":
            # Handles "=> not found" in a crude but safe way
            return VerifyResult(False, "libmpi not found in ldd output", exe)

        # Accept any concrete resolvable path, including system lib locations.
        # If it is elsewhere (for example a Spack store), accept and record it in the reason.
        libmpi_real = _rp(libmpi_path)

        # If it matches deps_prefix (string match) or resolves under it (realpath match), great.
        # Prefer reporting the canonical mpi_prefix when available.
        if mpi_prefix and libmpi_real.startswith(mpi_prefix + "/"):
            r = VerifyResult(True, f"ok (libmpi under mpi_prefix: {libmpi_real})", exe)
        elif deps_prefix in libmpi_path or deps_prefix in libmpi_real:
            r = VerifyResult(True, f"ok (libmpi under deps prefix: {libmpi_path})", exe)
        elif _is_system_path(libmpi_path):
            r = VerifyResult(True, f"ok (libmpi resolved from system path: {libmpi_path})", exe)
        else:
            r = VerifyResult(True, f"ok (libmpi resolved at: {libmpi_path})", exe)

        (out_dir / "postcheck.txt").write_text(
            f"ok={r.ok}\nreason={r.reason}\nlibmpi={libmpi_path}\nlibmpi_real={libmpi_real}\nmpiexec_real={mpiexec_real}\nmpi_prefix={mpi_prefix}\n"
        )
        return r

    return VerifyResult(True, "ok", exe)


def validate_mpas_success(repo_root: Path, deps_prefix: str | None, out_dir: Path) -> VerifyResult:
    return validate_core_success(repo_root, deps_prefix, out_dir, "mpas")
