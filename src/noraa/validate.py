from __future__ import annotations

from pathlib import Path
from .util import safe_check_output


class VerifyResult:
    def __init__(self, ok: bool, reason: str, mpas_exe: Path | None = None):
        self.ok = ok
        self.reason = reason
        self.mpas_exe = mpas_exe


def validate_mpas_success(repo_root: Path, deps_prefix: str | None, out_dir: Path) -> VerifyResult:
    mpas_exe = repo_root / ".noraa" / "build" / "bin" / "mpas_atmosphere"

    if not mpas_exe.exists():
        return VerifyResult(False, f"mpas_atmosphere missing at {mpas_exe}", None)

    if not mpas_exe.is_file() or not (mpas_exe.stat().st_mode & 0o111):
        return VerifyResult(False, f"mpas_atmosphere not executable: {mpas_exe}", mpas_exe)

    ldd = safe_check_output(["ldd", str(mpas_exe)])
    (out_dir / "ldd_mpas_atmosphere.txt").write_text(ldd)

    if deps_prefix and deps_prefix not in ldd:
        return VerifyResult(False, f"MPI libs not resolved under deps prefix {deps_prefix}", mpas_exe)

    return VerifyResult(True, "ok", mpas_exe)
