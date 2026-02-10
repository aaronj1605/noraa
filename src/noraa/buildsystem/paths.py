from __future__ import annotations

from pathlib import Path

from ..messages import fail, repo_cmd


def detect_verify_script(repo_root: Path) -> Path | None:
    candidates = [
        repo_root / "scripts" / "verify_mpas_smoke.sh",
        repo_root / "scripts" / "verify.sh",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def pick_mpas_suite(repo_root: Path) -> str:
    """Discover a valid MPAS CCPP suite, preferring suite_MPAS_RRFS.xml."""
    suites_dir = repo_root / "ccpp" / "suites"
    if not suites_dir.exists():
        fail(
            f"Missing ccpp/suites under {repo_root}",
            next_step=repo_cmd(repo_root, "verify"),
        )

    preferred = suites_dir / "suite_MPAS_RRFS.xml"
    if preferred.exists():
        return preferred.stem

    mpas = sorted(suites_dir.glob("suite_MPAS*.xml"))
    if mpas:
        return mpas[0].stem

    fail(
        "No MPAS suite XML found under ccpp/suites. "
        "Expected something like suite_MPAS_RRFS.xml.",
        next_step=repo_cmd(repo_root, "verify"),
    )


def bootstrapped_esmf_mk(repo_root: Path) -> Path | None:
    install_root = repo_root / ".noraa" / "esmf" / "install"
    if not install_root.exists():
        return None
    for mk in install_root.rglob("esmf.mk"):
        return mk
    return None


def bootstrapped_deps_prefix(repo_root: Path) -> Path | None:
    install_root = repo_root / ".noraa" / "deps" / "install"
    if install_root.exists():
        return install_root
    return None


def resolve_deps_prefix(repo_root: Path, deps_prefix: str | None) -> str | None:
    if deps_prefix:
        p = Path(deps_prefix)
        if p.exists():
            return str(p)

    p = bootstrapped_deps_prefix(repo_root)
    if p:
        return str(p)
    return None


def resolve_esmf_mkfile(
    repo_root: Path, deps_prefix: str | None, esmf_mkfile: str | None
) -> str:
    if esmf_mkfile:
        p = Path(esmf_mkfile)
        if p.exists():
            return str(p)

    mk = bootstrapped_esmf_mk(repo_root)
    if mk:
        return str(mk)

    if deps_prefix:
        candidate = Path(deps_prefix) / "lib" / "esmf.mk"
        if candidate.exists():
            return str(candidate)

    fail(
        "ESMF not found under .noraa/esmf/install and no valid --esmf-mkfile was provided.",
        next_step=repo_cmd(repo_root, "bootstrap", "esmf"),
    )

