from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # py311+
except Exception:  # pragma: no cover
    tomllib = None

from ..buildsystem.paths import bootstrapped_deps_prefix, bootstrapped_esmf_mk
from ..messages import repo_cmd
from ..util import safe_check_output


@dataclass(frozen=True)
class StatusCheck:
    name: str
    ok: bool
    detail: str
    action_required: str | None = None


@dataclass(frozen=True)
class DatasetCandidate:
    name: str
    ic_file: Path
    lbc_file: Path | None
    source_repo_path: Path
    source_repo_url: str


def _git_origin(path: Path) -> str:
    try:
        return safe_check_output(
            ["git", "-C", str(path), "config", "--get", "remote.origin.url"]
        ).strip()
    except Exception:
        return ""


def _search_roots(repo_root: Path) -> list[tuple[Path, str]]:
    roots: list[tuple[Path, str]] = []
    roots.append((repo_root, _git_origin(repo_root)))

    mpas_repo = repo_root / "mpas" / "MPAS-Model"
    if mpas_repo.exists():
        roots.append((mpas_repo, _git_origin(mpas_repo)))
    return roots


def _candidate_name(path: Path) -> str:
    return path.stem.replace(" ", "_")


def discover_dataset_candidates(repo_root: Path) -> list[DatasetCandidate]:
    candidates: list[DatasetCandidate] = []
    include_dirs = ("data", "test", "tests", "example", "examples", "input", "inputs")

    for root, origin in _search_roots(repo_root):
        if not root.exists():
            continue
        for nc in root.rglob("*.nc"):
            low = str(nc).replace("\\", "/").lower()
            if not any(f"/{d}/" in low for d in include_dirs):
                continue
            if not any(k in nc.name.lower() for k in ("init", "ic")):
                continue

            lbc_file = None
            for sib in nc.parent.glob("*.nc"):
                s = sib.name.lower()
                if sib != nc and ("lbc" in s or "bound" in s):
                    lbc_file = sib
                    break

            candidates.append(
                DatasetCandidate(
                    name=_candidate_name(nc),
                    ic_file=nc,
                    lbc_file=lbc_file,
                    source_repo_path=root,
                    source_repo_url=origin,
                )
            )

    # Stable deterministic order
    return sorted(candidates, key=lambda c: (str(c.source_repo_path), str(c.ic_file)))


def _dataset_manifest_path(repo_root: Path) -> Path:
    return repo_root / ".noraa" / "runs" / "smoke" / "data" / "dataset.toml"


def _dataset_root(repo_root: Path) -> Path:
    return repo_root / ".noraa" / "runs" / "smoke" / "data"


def _load_dataset_manifest(repo_root: Path) -> dict | None:
    manifest = _dataset_manifest_path(repo_root)
    if not manifest.exists() or tomllib is None:
        return None
    try:
        return tomllib.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None


def _smoke_data_ready(repo_root: Path) -> tuple[bool, str]:
    manifest = _load_dataset_manifest(repo_root)
    root = _dataset_root(repo_root)
    if manifest is None:
        return False, str(root)

    dataset = manifest.get("dataset", {})
    ic_rel = dataset.get("ic_file")
    if not ic_rel:
        return False, str(root)

    ic_path = root / ic_rel
    if not ic_path.exists():
        return False, str(ic_path)

    lbc_rel = dataset.get("lbc_file")
    if lbc_rel:
        lbc_path = root / lbc_rel
        if not lbc_path.exists():
            return False, str(lbc_path)

    return True, str(_dataset_manifest_path(repo_root))


def fetch_dataset(
    *,
    repo_root: Path,
    candidate: DatasetCandidate,
) -> Path:
    data_root = _dataset_root(repo_root)
    data_root.mkdir(parents=True, exist_ok=True)
    selected = data_root / candidate.name
    selected.mkdir(parents=True, exist_ok=True)

    ic_dest = selected / candidate.ic_file.name
    shutil.copy2(candidate.ic_file, ic_dest)

    lbc_dest = None
    if candidate.lbc_file is not None:
        lbc_dest = selected / candidate.lbc_file.name
        shutil.copy2(candidate.lbc_file, lbc_dest)

    source_repo = (candidate.source_repo_url or str(candidate.source_repo_path)).replace("\\", "/")
    source_path = str(candidate.ic_file).replace("\\", "/")
    manifest = _dataset_manifest_path(repo_root)
    lbc_line = (
        f'lbc_file = "{candidate.name}/{candidate.lbc_file.name}"\n'
        if candidate.lbc_file is not None
        else ""
    )
    manifest.write_text(
        "[dataset]\n"
        f'name = "{candidate.name}"\n'
        f'source_repo = "{source_repo}"\n'
        f'source_path = "{source_path}"\n'
        f'ic_file = "{candidate.name}/{candidate.ic_file.name}"\n'
        f"{lbc_line}",
        encoding="utf-8",
    )
    return manifest


def collect_status_checks(repo_root: Path) -> list[StatusCheck]:
    project_file = repo_root / ".noraa" / "project.toml"
    ccpp_prebuild = repo_root / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    deps_prefix = bootstrapped_deps_prefix(repo_root)
    esmf_mk = bootstrapped_esmf_mk(repo_root)
    mpas_exe = repo_root / ".noraa" / "build" / "bin" / "mpas_atmosphere"
    smoke_ok, smoke_detail = _smoke_data_ready(repo_root)

    return [
        StatusCheck(
            name="Project initialized",
            ok=project_file.exists(),
            detail=str(project_file),
            action_required=repo_cmd(repo_root, "init"),
        ),
        StatusCheck(
            name="Required CCPP submodule content",
            ok=ccpp_prebuild.exists(),
            detail=str(ccpp_prebuild),
            action_required="git submodule update --init --recursive",
        ),
        StatusCheck(
            name="ESMF dependency",
            ok=esmf_mk is not None,
            detail=str(esmf_mk) if esmf_mk else str(repo_root / ".noraa" / "esmf" / "install"),
            action_required=repo_cmd(repo_root, "bootstrap", "esmf"),
        ),
        StatusCheck(
            name="MPAS dependency bundle",
            ok=deps_prefix is not None,
            detail=str(deps_prefix) if deps_prefix else str(repo_root / ".noraa" / "deps" / "install"),
            action_required=repo_cmd(repo_root, "bootstrap", "deps"),
        ),
        StatusCheck(
            name="Verified MPAS executable",
            ok=mpas_exe.exists(),
            detail=str(mpas_exe),
            action_required=repo_cmd(repo_root, "verify"),
        ),
        StatusCheck(
            name="Smoke-run sample data",
            ok=smoke_ok,
            detail=smoke_detail,
            action_required=repo_cmd(repo_root, "run-smoke", "fetch-data"),
        ),
    ]


def format_status_report(checks: list[StatusCheck]) -> tuple[str, bool]:
    all_ok = all(c.ok for c in checks)
    lines = ["Run-smoke readiness status:"]
    for c in checks:
        flag = "GREEN" if c.ok else "RED"
        lines.append(f"{flag}: {c.name} [{c.detail}]")
        if not c.ok and c.action_required:
            lines.append(f"Action required: {c.action_required}")
    lines.append("READY: all required checks passed." if all_ok else "NOT READY: fix RED items before run-smoke execute.")
    return "\n".join(lines), all_ok
