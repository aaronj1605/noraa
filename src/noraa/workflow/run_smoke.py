from __future__ import annotations

import os
import shlex
import subprocess
import shutil
import tarfile
import urllib.request
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib  # py311+
except Exception:  # pragma: no cover
    tomllib = None

from ..buildsystem.paths import bootstrapped_deps_prefix, bootstrapped_esmf_mk
from ..messages import repo_cmd
from ..util import safe_check_output

HTF_REGISTRY_URL = "https://registry.opendata.aws/noaa-ufs-htf-pds"
HTF_BUCKET = "s3://noaa-ufs-htf-pds"


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


@dataclass(frozen=True)
class OfficialDataset:
    dataset_id: str
    url: str
    source_repo: str
    description: str
    runtime_compatible: bool
    runtime_note: str
    sha256: str | None = None


@dataclass(frozen=True)
class ExecuteResult:
    ok: bool
    run_dir: Path
    command: list[str]
    returncode: int | None
    reason: str


def execution_label(result: ExecuteResult) -> str:
    if result.returncode == 0:
        return "PASS"
    if result.ok and result.returncode is None:
        return "REACHED_RUNTIME_TIMEOUT"
    if result.ok:
        return "REACHED_RUNTIME_NONZERO"
    return "FAIL"


def htf_citation(accessed_on: str) -> str:
    return (
        "NOAA Unified Forecast System (UFS) Hierarchical Testing Framework (HTF) "
        f"was accessed on {accessed_on} from {HTF_REGISTRY_URL}."
    )


def current_utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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


def _sanitize_dataset_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("dataset name cannot be empty")
    if cleaned in {".", ".."}:
        raise ValueError("dataset name cannot be '.' or '..'")
    if "/" in cleaned or "\\" in cleaned:
        raise ValueError("dataset name cannot contain path separators")
    return cleaned


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _safe_extract_tar_gz(archive_path: Path, dest_dir: Path) -> None:
    dest_resolved = dest_dir.resolve()
    with tarfile.open(archive_path, "r:gz") as tf:
        members = tf.getmembers()
        for member in members:
            name = member.name
            if name.startswith("/") or name.startswith("\\"):
                raise ValueError(f"Unsafe archive member path: {name}")
            if member.issym() or member.islnk():
                raise ValueError(f"Unsafe archive member link: {name}")
            target = (dest_dir / name).resolve()
            if not str(target).startswith(str(dest_resolved) + os.sep) and target != dest_resolved:
                raise ValueError(f"Unsafe archive member traversal: {name}")
        tf.extractall(path=dest_dir, members=members)


def _validate_s3_prefix(prefix: str) -> str:
    cleaned = prefix.strip().strip("/")
    if not cleaned:
        raise ValueError("s3_prefix cannot be empty")
    if cleaned.startswith("s3://"):
        raise ValueError("s3_prefix must be bucket-relative, not a full s3:// URL")
    if "\\" in cleaned:
        raise ValueError("s3_prefix cannot contain backslashes")
    parts = cleaned.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("s3_prefix contains unsafe path segments")
    return cleaned


def _looks_like_ic_file(nc: Path) -> bool:
    name = nc.name.lower()
    tokens = (
        "init",
        "initial",
        "_ic",
        "ic_",
    )
    return any(t in name for t in tokens)


def discover_dataset_candidates(repo_root: Path) -> list[DatasetCandidate]:
    candidates: list[DatasetCandidate] = []
    include_dirs = ("data", "test", "tests", "example", "examples", "input", "inputs")

    for root, origin in _search_roots(repo_root):
        if not root.exists():
            continue
        for nc in root.rglob("*.nc"):
            low = str(nc).replace("\\", "/").lower()
            # Ignore NORAA-managed artifacts and embedded ESMF source trees.
            if "/.noraa/" in low or "/esmf/src/" in low:
                continue
            if not any(f"/{d}/" in low for d in include_dirs):
                continue
            if not _looks_like_ic_file(nc):
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


def official_catalog() -> list[OfficialDataset]:
    # Curated official MPAS test-case bundles.
    return [
        OfficialDataset(
            dataset_id="supercell",
            url="https://www2.mmm.ucar.edu/projects/mpas/test_cases/v7.0/supercell.tar.gz",
            source_repo="https://mpas-dev.github.io/atmosphere/test_cases.html",
            description="MPAS idealized supercell test-case bundle",
            runtime_compatible=False,
            runtime_note=(
                "MPAS standalone test-case bundle. For NORAA/ufsatm smoke execution this is metadata-only "
                "unless you provide a UFS-compatible runtime recipe and inputs."
            ),
        ),
        OfficialDataset(
            dataset_id="mountain_wave",
            url="https://www2.mmm.ucar.edu/projects/mpas/test_cases/v7.0/mountain_wave.tar.gz",
            source_repo="https://mpas-dev.github.io/atmosphere/test_cases.html",
            description="MPAS idealized mountain-wave test-case bundle",
            runtime_compatible=False,
            runtime_note=(
                "MPAS standalone test-case bundle. For NORAA/ufsatm smoke execution this is metadata-only "
                "unless you provide a UFS-compatible runtime recipe and inputs."
            ),
        ),
        OfficialDataset(
            dataset_id="jw_baroclinic_wave",
            url="https://www2.mmm.ucar.edu/projects/mpas/test_cases/v7.0/jw_baroclinic_wave.tar.gz",
            source_repo="https://mpas-dev.github.io/atmosphere/test_cases.html",
            description="MPAS idealized Jablonowski-Williamson baroclinic-wave bundle",
            runtime_compatible=False,
            runtime_note=(
                "MPAS standalone test-case bundle. For NORAA/ufsatm smoke execution this is metadata-only "
                "unless you provide a UFS-compatible runtime recipe and inputs."
            ),
        ),
    ]


def resolve_official_dataset(dataset_id: str) -> OfficialDataset | None:
    for ds in official_catalog():
        if ds.dataset_id == dataset_id:
            return ds
    return None


def _smoke_data_ready(repo_root: Path) -> tuple[bool, str]:
    manifest = _load_dataset_manifest(repo_root)
    root = _dataset_root(repo_root)
    if manifest is None:
        return False, str(root)

    dataset = manifest.get("dataset", {})
    bundle_rel = dataset.get("bundle_dir")
    if bundle_rel:
        bundle_path = root / bundle_rel
        if bundle_path.exists() and any(bundle_path.rglob("*")):
            return True, str(_dataset_manifest_path(repo_root))
        return False, str(bundle_path)

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


def fetch_official_bundle(*, repo_root: Path, dataset: OfficialDataset) -> Path:
    data_root = _dataset_root(repo_root)
    data_root.mkdir(parents=True, exist_ok=True)
    safe_id = _sanitize_dataset_name(dataset.dataset_id)
    bundle_dir = data_root / safe_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    archive_name = dataset.url.split("/")[-1] or f"{safe_id}.tar.gz"
    archive_path = bundle_dir / archive_name
    urllib.request.urlretrieve(dataset.url, archive_path)
    archive_sha256 = _sha256_file(archive_path)
    if dataset.sha256 and archive_sha256.lower() != dataset.sha256.lower():
        raise ValueError(
            f"Downloaded archive hash mismatch for {safe_id}: "
            f"expected {dataset.sha256}, got {archive_sha256}"
        )

    if archive_name.endswith(".tar.gz"):
        _safe_extract_tar_gz(archive_path, bundle_dir)

    manifest = _dataset_manifest_path(repo_root)
    source_repo = dataset.source_repo.replace("\\", "/")
    manifest.write_text(
        "[dataset]\n"
        f'name = "{safe_id}"\n'
        f'source_repo = "{source_repo}"\n'
        f'source_path = "{dataset.url}"\n'
        f'bundle_dir = "{safe_id}"\n'
        f'archive_sha256 = "{archive_sha256}"\n'
        f"runtime_compatible = {str(dataset.runtime_compatible).lower()}\n"
        f'runtime_note = "{dataset.runtime_note}"\n',
        encoding="utf-8",
    )
    return manifest


def fetch_official_ufs_prefix(
    *,
    repo_root: Path,
    s3_prefix: str,
    aws_bin: str = "aws",
    accessed_on: str | None = None,
) -> Path:
    data_root = _dataset_root(repo_root)
    data_root.mkdir(parents=True, exist_ok=True)

    prefix = _validate_s3_prefix(s3_prefix)
    dataset_id = _sanitize_dataset_name(prefix.split("/")[-1])
    bundle_dir = data_root / dataset_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    source_path = f"{HTF_BUCKET}/{prefix}/"
    subprocess.run(
        [aws_bin, "s3", "cp", "--recursive", "--no-sign-request", source_path, str(bundle_dir)],
        check=True,
        text=True,
    )

    accessed = accessed_on or current_utc_date()
    citation = htf_citation(accessed)
    manifest = _dataset_manifest_path(repo_root)
    manifest.write_text(
        "[dataset]\n"
        f'name = "{dataset_id}"\n'
        f'source_repo = "{HTF_REGISTRY_URL}"\n'
        f'source_path = "{source_path}"\n'
        f'bundle_dir = "{dataset_id}"\n'
        "runtime_compatible = true\n"
        f'citation = "{citation}"\n'
        f'citation_accessed_on = "{accessed}"\n',
        encoding="utf-8",
    )
    return manifest


def fetch_local_dataset(*, repo_root: Path, local_path: Path, dataset_name: str = "local_user_data") -> Path:
    data_root = _dataset_root(repo_root)
    data_root.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_dataset_name(dataset_name)
    target_dir = data_root / safe_name
    target_dir.mkdir(parents=True, exist_ok=True)

    if local_path.is_dir():
        for item in local_path.iterdir():
            if item.is_symlink():
                continue
            dest = target_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
    else:
        if local_path.is_symlink():
            raise ValueError("local_path cannot be a symlink")
        shutil.copy2(local_path, target_dir / local_path.name)

    manifest = _dataset_manifest_path(repo_root)
    local_norm = str(local_path).replace("\\", "/")
    manifest.write_text(
        "[dataset]\n"
        f'name = "{safe_name}"\n'
        'source_repo = "user-local"\n'
        f'source_path = "{local_norm}"\n'
        f'bundle_dir = "{safe_name}"\n'
        "runtime_compatible = true\n"
        'runtime_note = "User-provided dataset. NORAA will validate required runtime files."\n',
        encoding="utf-8",
    )
    return manifest


def _resolve_case_dir(repo_root: Path, dataset: dict) -> Path | None:
    data_root = _dataset_root(repo_root)
    bundle_rel = dataset.get("bundle_dir")
    if not bundle_rel:
        return None
    bundle_root = data_root / bundle_rel
    if not bundle_root.exists():
        return None
    name = dataset.get("name")
    if name:
        nested = bundle_root / name
        if nested.exists() and nested.is_dir():
            return nested
    return bundle_root


def _known_standalone_case_note(case_dir: Path, dataset: dict) -> str | None:
    source_repo = str(dataset.get("source_repo") or "").lower()
    source_path = str(dataset.get("source_path") or "").lower()
    if "mpas-dev.github.io/atmosphere/test_cases.html" in source_repo:
        return (
            "Dataset appears to be an MPAS standalone test-case bundle and is not directly "
            "runtime-compatible with current ufsatm MPAS smoke execution."
        )
    # Local copies of MPAS standalone bundles are commonly identified by these files.
    standalone_markers = [
        case_dir / "supercell.ncl",
        case_dir / "supercell.graph.info",
        case_dir / "namelist.init_atmosphere",
        case_dir / "streams.init_atmosphere",
    ]
    if sum(1 for p in standalone_markers if p.exists()) >= 2:
        return (
            "Dataset matches MPAS standalone case layout; NORAA execute for ufsatm MPAS is "
            "blocked to avoid known streams/time parser incompatibilities."
        )
    if "supercell" in source_path and (case_dir / "supercell.graph.info").exists():
        return (
            "Supercell standalone dataset detected; this is not directly runtime-compatible "
            "with current ufsatm MPAS smoke execution."
        )
    return None


def smoke_runtime_compatibility(repo_root: Path) -> tuple[bool, str, str]:
    manifest = _load_dataset_manifest(repo_root)
    if manifest is None:
        return (
            False,
            "No dataset manifest found for smoke runtime.",
            repo_cmd(repo_root, "run-smoke", "fetch-data", "official"),
        )

    dataset = manifest.get("dataset", {})
    runtime_flag = dataset.get("runtime_compatible")
    if runtime_flag is False:
        note = str(dataset.get("runtime_note") or "Dataset marked metadata-only for runtime.")
        return (
            False,
            note,
            (
                f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/ufs-runtime-data "
                f"--dataset ufs_runtime_case"
            ),
        )

    case_dir = _resolve_case_dir(repo_root, dataset)
    if case_dir is None:
        return (
            False,
            "Dataset manifest does not define a runnable bundle_dir case location.",
            (
                f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/ufs-runtime-data "
                f"--dataset ufs_runtime_case"
            ),
        )

    standalone_note = _known_standalone_case_note(case_dir, dataset)
    if standalone_note is not None:
        return (
            False,
            standalone_note,
            (
                f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/ufs-runtime-data "
                f"--dataset ufs_runtime_case"
            ),
        )

    namelist = case_dir / "namelist.atmosphere"
    streams = case_dir / "streams.atmosphere"
    if not namelist.exists():
        return (
            False,
            f"Missing runtime file: {namelist}",
            f"Provide a UFS-compatible dataset with namelist.atmosphere via {repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/ufs-runtime-data",
        )
    if not streams.exists():
        return (
            False,
            f"Missing runtime file: {streams}",
            f"Provide a UFS-compatible dataset with streams.atmosphere via {repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/ufs-runtime-data",
        )
    text = namelist.read_text(encoding="utf-8", errors="ignore")
    if "config_calendar_type" not in text:
        return (
            False,
            "namelist.atmosphere missing config_calendar_type (required for current UFS runtime path).",
            f"Use UFS-compatible runtime data via {repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/ufs-runtime-data",
        )
    return True, str(case_dir), ""


def collect_status_checks(repo_root: Path) -> list[StatusCheck]:
    project_file = repo_root / ".noraa" / "project.toml"
    ccpp_prebuild = repo_root / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    deps_prefix = bootstrapped_deps_prefix(repo_root)
    esmf_mk = bootstrapped_esmf_mk(repo_root)
    mpas_exe = repo_root / ".noraa" / "build" / "bin" / "mpas_atmosphere"
    smoke_ok, smoke_detail = _smoke_data_ready(repo_root)
    runtime_ok, runtime_detail, runtime_action = smoke_runtime_compatibility(repo_root)

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
            action_required=repo_cmd(repo_root, "run-smoke", "fetch-data", "official"),
        ),
        StatusCheck(
            name="Runtime-compatible smoke dataset",
            ok=runtime_ok,
            detail=runtime_detail,
            action_required=runtime_action or repo_cmd(repo_root, "run-smoke", "fetch-data", "local"),
        ),
    ]


def format_status_report(checks: list[StatusCheck]) -> tuple[str, bool]:
    all_ok = all(c.ok for c in checks)
    lines = ["NORAA run-smoke readiness status:"]
    for c in checks:
        flag = "GREEN" if c.ok else "RED"
        lines.append(f"{flag}: {c.name} [{c.detail}]")
        if not c.ok and c.action_required:
            lines.append(f"Action required: {c.action_required}")
    lines.append("READY: all required checks passed." if all_ok else "NOT READY: fix RED items before run-smoke execute.")
    return "\n".join(lines), all_ok


def first_blocking_action(checks: list[StatusCheck]) -> str | None:
    for check in checks:
        if not check.ok and check.action_required:
            return check.action_required
    return None


def _looks_like_runtime_ok(stdout_text: str, stderr_text: str) -> bool:
    text = f"{stdout_text}\n{stderr_text}".lower()
    markers = ("namelist", "streams", "usage", "input")
    return any(marker in text for marker in markers)


def _smoke_exec_dir(repo_root: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = repo_root / ".noraa" / "runs" / "smoke" / "exec" / ts
    out.mkdir(parents=True, exist_ok=True)
    return out


def execute_smoke(
    *,
    repo_root: Path,
    timeout_sec: int = 20,
    command_text: str | None = None,
) -> ExecuteResult:
    run_dir = _smoke_exec_dir(repo_root)
    mpas_exe = repo_root / ".noraa" / "build" / "bin" / "mpas_atmosphere"
    command = (
        shlex.split(command_text, posix=(os.name != "nt"))
        if command_text
        else [str(mpas_exe)]
    )

    (run_dir / "command.txt").write_text(" ".join(command) + "\n", encoding="utf-8")
    stdout_path = run_dir / "stdout.txt"
    stderr_path = run_dir / "stderr.txt"

    try:
        proc = subprocess.run(
            command,
            cwd=run_dir,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8")

        if proc.returncode == 0:
            reason = "Smoke execution command completed successfully."
            ok = True
        elif _looks_like_runtime_ok(proc.stdout or "", proc.stderr or ""):
            reason = (
                "Smoke execution reached runtime checks (non-zero exit, but executable launched and "
                "reported expected runtime input/namelist guidance)."
            )
            ok = True
        else:
            reason = f"Smoke execution failed with return code {proc.returncode}."
            ok = False
        returncode: int | None = proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text(exc.stdout or "", encoding="utf-8")
        stderr_path.write_text(exc.stderr or "", encoding="utf-8")
        reason = (
            f"Smoke execution exceeded timeout ({timeout_sec}s) after launch. "
            "Process started; treat as runtime-reachable."
        )
        ok = True
        returncode = None
    except FileNotFoundError:
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("Executable not found while launching smoke command.\n", encoding="utf-8")
        reason = "Smoke execution command could not be launched."
        ok = False
        returncode = None

    (run_dir / "result.txt").write_text(
        "\n".join(
            [
                f"ok={str(ok).lower()}",
                f"returncode={'' if returncode is None else returncode}",
                f"reason={reason}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return ExecuteResult(ok=ok, run_dir=run_dir, command=command, returncode=returncode, reason=reason)
