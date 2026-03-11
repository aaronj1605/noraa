from __future__ import annotations

import os
import shlex
import subprocess
import shutil
import re
import tarfile
import urllib.request
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    import tomllib  # py311+
except Exception:  # pragma: no cover
    tomllib = None

from ..buildsystem.paths import bootstrapped_deps_prefix, bootstrapped_esmf_mk
from ..messages import repo_cmd
from ..project import load_project
from ..util import safe_check_output

HTF_REGISTRY_URL = "https://registry.opendata.aws/noaa-ufs-htf-pds"
HTF_BUCKET = "s3://noaa-ufs-htf-pds"
REGTESTS_SOURCE_URL = "https://noaa-ufs-regtests-pds.s3.amazonaws.com"
REGTESTS_BUCKET = "s3://noaa-ufs-regtests-pds"


@dataclass(frozen=True)
class StatusCheck:
    name: str
    ok: bool
    detail: str
    action_required: str | None = None
    applicable: bool = True


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
    artifacts: tuple[str, ...] = ()


def execution_label(result: ExecuteResult) -> str:
    if result.ok and result.artifacts:
        return "PASS_WITH_OUTPUT"
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
        total_members = len(members)
        total_bytes = sum(m.size for m in members if m.isreg())
        for member in members:
            name = member.name
            if name.startswith("/") or name.startswith("\\"):
                raise ValueError(f"Unsafe archive member path: {name}")
            if member.issym() or member.islnk():
                raise ValueError(f"Unsafe archive member link: {name}")
            target = (dest_dir / name).resolve()
            if not str(target).startswith(str(dest_resolved) + os.sep) and target != dest_resolved:
                raise ValueError(f"Unsafe archive member traversal: {name}")
        print(
            "Extracting archive "
            f"({total_members} entries, {total_bytes / (1024 * 1024):.1f} MiB payload)..."
        )
        extracted_bytes = 0
        # Emit progress every ~5%.
        thresholds = [i / 20 for i in range(1, 21)]
        next_threshold_idx = 0
        for idx, member in enumerate(members, start=1):
            try:
                tf.extract(member, path=dest_dir, filter="data")
            except TypeError:
                tf.extract(member, path=dest_dir)
            if member.isreg():
                extracted_bytes += member.size
            if total_bytes > 0 and next_threshold_idx < len(thresholds):
                progress = extracted_bytes / total_bytes
                if progress >= thresholds[next_threshold_idx]:
                    pct = int(thresholds[next_threshold_idx] * 100)
                    print(f"Extract progress: {pct}% ({idx}/{total_members} entries)")
                    next_threshold_idx += 1
        print("Extract complete.")


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


def _download_url_with_progress(url: str, dest_path: Path) -> None:
    basename = Path(urlparse(url).path).name or dest_path.name
    print(f"Downloading {basename} ...")

    last_pct = {"value": -1}

    def _hook(block_count: int, block_size: int, total_size: int) -> None:
        if total_size <= 0:
            # Unknown size; print sparse heartbeat.
            if block_count % 2048 == 0:
                print(f"Download in progress: {block_count * block_size / (1024 * 1024):.1f} MiB")
            return
        downloaded = block_count * block_size
        pct = int(min(100, (downloaded * 100) / total_size))
        if pct >= last_pct["value"] + 5:
            last_pct["value"] = pct
            print(f"Download progress: {pct}%")

    try:
        urllib.request.urlretrieve(url, dest_path, _hook)
    except TypeError:
        # Some test doubles or environments expose a 2-arg urlretrieve.
        urllib.request.urlretrieve(url, dest_path)
    print("Download complete.")


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


def _selected_core(repo_root: Path) -> str:
    cfg = load_project(repo_root)
    core = (cfg.core if cfg else "mpas").strip().lower()
    return core if core in {"mpas", "fv3"} else "mpas"


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
    _download_url_with_progress(dataset.url, archive_path)
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


def fetch_official_regtests_prefix(
    *,
    repo_root: Path,
    s3_prefix: str,
    aws_bin: str = "aws",
) -> Path:
    data_root = _dataset_root(repo_root)
    data_root.mkdir(parents=True, exist_ok=True)

    prefix = _validate_s3_prefix(s3_prefix)
    dataset_id = _sanitize_dataset_name(prefix.replace("/", "_"))
    bundle_dir = data_root / dataset_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    source_path = f"{REGTESTS_BUCKET}/{prefix}/"
    subprocess.run(
        [aws_bin, "s3", "cp", "--recursive", "--no-sign-request", source_path, str(bundle_dir)],
        check=True,
        text=True,
    )

    manifest = _dataset_manifest_path(repo_root)
    manifest.write_text(
        "[dataset]\n"
        f'name = "{dataset_id}"\n'
        f'source_repo = "{REGTESTS_SOURCE_URL}"\n'
        f'source_path = "{source_path}"\n'
        f'bundle_dir = "{dataset_id}"\n'
        "runtime_compatible = true\n"
        'runtime_note = "Fetched from noaa-ufs-regtests-pds. NORAA will validate required runtime files."\n',
        encoding="utf-8",
    )
    return manifest


def list_official_regtests_prefixes(
    *,
    aws_bin: str = "aws",
    catalog_root: str = "input-data-20251015",
) -> list[str]:
    """
    Discover one-level dataset prefixes under the regtests bucket catalog root.
    Returns values like: input-data-20251015/FV3_regional_input_data
    """
    root = _validate_s3_prefix(catalog_root)
    source_path = f"{REGTESTS_BUCKET}/{root}/"
    out = subprocess.check_output(
        [aws_bin, "s3", "ls", "--no-sign-request", source_path],
        text=True,
        stderr=subprocess.STDOUT,
    )
    prefixes: list[str] = []
    for line in out.splitlines():
        m = re.search(r"PRE\s+(.+?)/\s*$", line)
        if not m:
            continue
        child = m.group(1).strip()
        if not child or child in {".", ".."}:
            continue
        prefixes.append(f"{root}/{child}")
    return sorted(set(prefixes))


def regtests_prefix_size_bytes(*, s3_prefix: str, aws_bin: str = "aws") -> int | None:
    """
    Return total size in bytes for a regtests prefix via `aws s3 ls --summarize`.
    Returns None when size cannot be parsed.
    """
    prefix = _validate_s3_prefix(s3_prefix)
    source_path = f"{REGTESTS_BUCKET}/{prefix}/"
    out = subprocess.check_output(
        [aws_bin, "s3", "ls", "--no-sign-request", "--recursive", "--summarize", source_path],
        text=True,
        stderr=subprocess.STDOUT,
    )
    for line in reversed(out.splitlines()):
        m = re.search(r"Total Size:\s*([0-9]+)", line)
        if m:
            return int(m.group(1))
    return None


def _extract_start_time_hint(namelist_path: Path) -> str:
    default = "2010-10-23_00:00:00"
    if not namelist_path.exists():
        return default
    text = namelist_path.read_text(encoding="utf-8", errors="ignore")
    for key in ("config_start_time", "mpas_start_time"):
        m = re.search(rf"{key}\s*=\s*['\"]([^'\"]+)['\"]", text)
        if m:
            return m.group(1).strip() or default
    return default


def _inject_input_reference_time(streams_text: str, reference_time: str) -> tuple[str, bool]:
    block_match = re.search(
        r'(<immutable_stream[^>]*name\s*=\s*"input"[\s\S]*?/>)',
        streams_text,
        flags=re.IGNORECASE,
    )
    if block_match is None:
        return streams_text, False
    block = block_match.group(1)
    if "reference_time=" in block:
        return streams_text, False
    if "input_interval=" in block:
        updated_block = re.sub(
            r"(\s+input_interval=)",
            f' reference_time="{reference_time}"\\1',
            block,
            count=1,
        )
    else:
        updated_block = block.replace(
            "/>",
            f'\n                  reference_time="{reference_time}"/>',
            1,
        )
    return (
        streams_text[: block_match.start()] + updated_block + streams_text[block_match.end() :],
        True,
    )


def _apply_local_mpas_compat_fixes(case_dir: Path) -> list[str]:
    fixes: list[str] = []
    namelist = case_dir / "namelist.atmosphere"
    streams = case_dir / "streams.atmosphere"
    if not streams.exists():
        return fixes

    reference_time = _extract_start_time_hint(namelist)
    original = streams.read_text(encoding="utf-8", errors="ignore")
    updated, changed = _inject_input_reference_time(original, reference_time)
    if changed:
        streams.write_text(updated, encoding="utf-8")
        fixes.append(f'Added reference_time="{reference_time}" to input stream')

    try:
        missing_refs = _missing_stream_references(case_dir, streams)
    except Exception:
        missing_refs = []
    case_root = case_dir.resolve()
    for p in missing_refs:
        try:
            resolved = p.resolve()
        except Exception:
            continue
        if not str(resolved).startswith(str(case_root) + os.sep):
            continue
        name = p.name.lower()
        if name in {"sfc_update.nc", "x1.40962.sfc_update.nc"}:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch(exist_ok=True)
            fixes.append(f"Created placeholder runtime file: {p.name}")
    return fixes


def fetch_local_dataset(
    *,
    repo_root: Path,
    local_path: Path,
    dataset_name: str = "local_user_data",
    auto_fix_mpas_compat: bool = True,
) -> Path:
    data_root = _dataset_root(repo_root)
    data_root.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_dataset_name(dataset_name)
    target_dir = data_root / safe_name
    # Replace existing dataset contents to avoid stale-file carryover from prior imports.
    if target_dir.exists():
        shutil.rmtree(target_dir)
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

    fixes: list[str] = []
    if auto_fix_mpas_compat and local_path.is_dir():
        fixes = _apply_local_mpas_compat_fixes(target_dir)

    manifest = _dataset_manifest_path(repo_root)
    local_norm = str(local_path).replace("\\", "/")
    escaped = [f.replace("\\", "\\\\").replace('"', '\\"') for f in fixes]
    fixes_line = ""
    if escaped:
        fixes_line = "compat_fixes = [" + ", ".join(f'"{x}"' for x in escaped) + "]\n"
    manifest.write_text(
        "[dataset]\n"
        f'name = "{safe_name}"\n'
        'source_repo = "user-local"\n'
        f'source_path = "{local_norm}"\n'
        f'bundle_dir = "{safe_name}"\n'
        "runtime_compatible = true\n"
        f"{fixes_line}"
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

    ok, detail = validate_runtime_case_dir(case_dir)
    if not ok:
        if detail.startswith("Missing runtime file:"):
            if "namelist.atmosphere" in detail:
                action = (
                    f"Provide a UFS-compatible dataset with namelist.atmosphere via "
                    f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/ufs-runtime-data"
                )
            elif "streams.atmosphere" in detail:
                action = (
                    f"Provide a UFS-compatible dataset with streams.atmosphere via "
                    f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/ufs-runtime-data"
                )
            else:
                action = (
                    f"Use UFS-compatible runtime data via "
                    f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/ufs-runtime-data"
                )
        else:
            action = (
                f"Use UFS-compatible runtime data via "
                f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/ufs-runtime-data"
            )
        return False, detail, action
    return True, str(case_dir), ""


def _collect_stream_path_tokens(streams_text: str) -> list[str]:
    tokens = re.findall(r'["\']([^"\']+)["\']', streams_text)
    out: list[str] = []
    for raw in tokens:
        t = raw.strip()
        if not t:
            continue
        if t.endswith("/"):
            continue
        if t.startswith(("http://", "https://", "s3://")):
            continue
        # Ignore templated/output patterns.
        if any(ch in t for ch in ("$", "{", "}", "%", "<", ">")):
            continue
        low = t.lower()
        if (
            "/" in t
            or "\\" in t
            or low.endswith(
                (
                    ".nc",
                    ".info",
                    ".dat",
                    ".bin",
                    ".txt",
                    ".yaml",
                    ".yml",
                    ".xml",
                )
            )
        ):
            out.append(t)
    # Preserve order and remove duplicates.
    unique: list[str] = []
    seen: set[str] = set()
    for t in out:
        if t in seen:
            continue
        seen.add(t)
        unique.append(t)
    return unique


def _missing_stream_references(case_dir: Path, streams_path: Path) -> list[Path]:
    text = streams_path.read_text(encoding="utf-8", errors="ignore")
    missing: list[Path] = []
    for token in _collect_stream_path_tokens(text):
        p = Path(token)
        candidate = p if p.is_absolute() else case_dir / p
        if not candidate.exists():
            missing.append(candidate)
    return missing


def validate_runtime_case_dir(case_dir: Path) -> tuple[bool, str]:
    namelist = case_dir / "namelist.atmosphere"
    streams = case_dir / "streams.atmosphere"
    if not namelist.exists():
        return False, f"Missing runtime file: {namelist}"
    if not streams.exists():
        return False, f"Missing runtime file: {streams}"
    text = namelist.read_text(encoding="utf-8", errors="ignore")
    if "config_calendar_type" not in text:
        return (
            False,
            "namelist.atmosphere missing config_calendar_type (required for current UFS runtime path).",
        )
    missing_refs = _missing_stream_references(case_dir, streams)
    if missing_refs:
        sample = ", ".join(str(p) for p in missing_refs[:5])
        extra = "" if len(missing_refs) <= 5 else f" (+{len(missing_refs) - 5} more)"
        return (
            False,
            f"streams.atmosphere references missing runtime files: {sample}{extra}",
        )
    return True, str(case_dir)


def validate_fv3_runtime_case_dir(case_dir: Path) -> tuple[bool, str]:
    required = [
        case_dir / "input.nml",
        case_dir / "model_configure",
        case_dir / "ufs.configure",
        case_dir / "diag_table",
        case_dir / "field_table",
    ]
    for path in required:
        if not path.exists():
            return False, f"Missing runtime file: {path}"
    if not (case_dir / "INPUT").exists() and not (case_dir / "RESTART").exists():
        return (
            False,
            (
                "Missing runtime directory: expected INPUT/ or RESTART/ in "
                f"{case_dir}"
            ),
        )
    return True, str(case_dir)


def validate_runtime_data(repo_root: Path) -> tuple[bool, str, str]:
    return smoke_runtime_compatibility(repo_root)


def local_dataset_compat_fixes(repo_root: Path) -> list[str]:
    manifest = _load_dataset_manifest(repo_root)
    if manifest is None:
        return []
    dataset = manifest.get("dataset", {})
    fixes = dataset.get("compat_fixes")
    if not isinstance(fixes, list):
        return []
    return [str(x) for x in fixes]


def collect_status_checks(repo_root: Path) -> list[StatusCheck]:
    core = _selected_core(repo_root)
    project_file = repo_root / ".noraa" / "project.toml"
    ccpp_prebuild = repo_root / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    deps_prefix = bootstrapped_deps_prefix(repo_root)
    esmf_mk = bootstrapped_esmf_mk(repo_root)
    if core == "fv3":
        exe_candidates = [
            repo_root / ".noraa" / "build" / "bin" / "ufs_model",
            repo_root / ".noraa" / "build" / "ufs_model",
            repo_root / "build" / "ufs_model",
            repo_root / ".noraa" / "build" / "ufsatm" / "libufsatm_fv3.a",
            repo_root / ".noraa" / "build" / "ufsatm" / "libufsatm_fv3.so",
        ]
        verify_action = f"{repo_cmd(repo_root, 'verify')} --core fv3"
        smoke_ok, smoke_detail = True, "Not applicable for core=fv3."
        runtime_ok, runtime_detail, runtime_action = (
            True,
            "Not applicable for core=fv3 (use --command with a valid FV3/UFS runtime directory).",
            "",
        )
        deps_ok = True
        deps_detail = "Not applicable for core=fv3."
        deps_action = None
    else:
        exe_candidates = [repo_root / ".noraa" / "build" / "bin" / "mpas_atmosphere"]
        verify_action = f"{repo_cmd(repo_root, 'verify')} --core mpas"
        smoke_ok, smoke_detail = _smoke_data_ready(repo_root)
        runtime_ok, runtime_detail, runtime_action = smoke_runtime_compatibility(repo_root)
        deps_ok = deps_prefix is not None
        deps_detail = str(deps_prefix) if deps_prefix else str(repo_root / ".noraa" / "deps" / "install")
        deps_action = repo_cmd(repo_root, "bootstrap", "deps")
    selected_exe = next((p for p in exe_candidates if p.exists()), exe_candidates[0])

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
            ok=deps_ok,
            detail=deps_detail,
            action_required=deps_action,
            applicable=(core == "mpas"),
        ),
        StatusCheck(
            name=f"Verified {core.upper()} executable",
            ok=selected_exe.exists(),
            detail=str(selected_exe),
            action_required=verify_action,
        ),
        StatusCheck(
            name="Smoke-run sample data",
            ok=smoke_ok,
            detail=smoke_detail,
            action_required=repo_cmd(repo_root, "run-smoke", "fetch-data", "official"),
            applicable=(core == "mpas"),
        ),
        StatusCheck(
            name="Runtime-compatible smoke dataset",
            ok=runtime_ok,
            detail=runtime_detail,
            action_required=runtime_action or repo_cmd(repo_root, "run-smoke", "fetch-data", "local"),
            applicable=(core == "mpas"),
        ),
    ]


def format_status_report(checks: list[StatusCheck]) -> tuple[str, bool]:
    all_ok = all(c.ok for c in checks if c.applicable)
    lines = ["NORAA run-smoke readiness status:"]
    for c in checks:
        if not c.applicable:
            flag = "N/A"
        else:
            flag = "GREEN" if c.ok else "RED"
        lines.append(f"{flag}: {c.name} [{c.detail}]")
        if c.applicable and (not c.ok) and c.action_required:
            lines.append(f"Action required: {c.action_required}")
    lines.append("READY: all required checks passed." if all_ok else "NOT READY: fix RED items before run-smoke execute.")
    return "\n".join(lines), all_ok


def format_status_short(checks: list[StatusCheck]) -> tuple[str, bool]:
    all_ok = all(c.ok for c in checks if c.applicable)
    if all_ok:
        return "READY", True
    failing = [c.name for c in checks if c.applicable and not c.ok]
    return "NOT READY: " + "; ".join(failing), False


def first_blocking_action(checks: list[StatusCheck]) -> str | None:
    for check in checks:
        if check.applicable and (not check.ok) and check.action_required:
            return check.action_required
    return None


def _looks_like_runtime_ok(stdout_text: str, stderr_text: str) -> bool:
    text = f"{stdout_text}\n{stderr_text}".lower()
    markers = ("namelist", "streams", "usage", "input")
    return any(marker in text for marker in markers)


def _looks_like_runtime_ok_from_logs(run_dir: Path) -> bool:
    """
    MPAS often writes startup/runtime diagnostics into log.atmosphere.*.out
    instead of stdout/stderr. Treat those as runtime-reached signals.
    """
    markers = (
        "reading namelist from file namelist.atmosphere",
        "initializing mpas_streaminfo from file streams.atmosphere",
        "reading streams configuration from file streams.atmosphere",
        "bootstrapping framework with mesh fields",
    )
    for p in run_dir.glob("log.atmosphere.*.out"):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            continue
        if any(m in text for m in markers):
            return True
    return False


def _collect_output_artifacts(run_dir: Path) -> tuple[str, ...]:
    patterns = (
        "history*.nc",
        "diag*.nc",
        "restart*.nc",
        "output*.nc",
    )
    found: list[str] = []
    for pattern in patterns:
        for p in sorted(run_dir.glob(pattern)):
            if p.is_file():
                found.append(p.name)
    return tuple(dict.fromkeys(found))


def _remove_stale_output_artifacts(run_dir: Path) -> None:
    for pattern in ("history*.nc", "diag*.nc", "restart*.nc", "output*.nc"):
        for p in run_dir.glob(pattern):
            if p.is_file():
                try:
                    p.unlink()
                except Exception:
                    continue


def _fatal_runtime_marker(stdout_text: str, stderr_text: str, run_dir: Path) -> str | None:
    blob = f"{stdout_text}\n{stderr_text}".lower()
    for p in run_dir.glob("log.atmosphere.*.out"):
        try:
            blob += "\n" + p.read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            continue
    for p in run_dir.glob("log.atmosphere.*.err"):
        try:
            blob += "\n" + p.read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            continue
    if "xml stream parser failed" in blob:
        return "xml stream parser failed"
    if "need real calendar" in blob:
        return "esmf calendar initialization failed (need real calendar)"
    if "invalid datetime string" in blob:
        return "invalid datetime string in runtime inputs/streams"
    if "could not open input file" in blob:
        return "runtime input file open failed"
    return None


def _smoke_exec_dir(repo_root: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = repo_root / ".noraa" / "runs" / "smoke" / "exec" / ts
    out.mkdir(parents=True, exist_ok=True)
    return out


def _stage_case_into_run_dir(repo_root: Path, run_dir: Path) -> Path | None:
    manifest = _load_dataset_manifest(repo_root)
    if manifest is None:
        return None
    dataset = manifest.get("dataset", {})
    case_dir = _resolve_case_dir(repo_root, dataset)
    if case_dir is None or not case_dir.exists() or not case_dir.is_dir():
        return None
    for item in case_dir.iterdir():
        if item.is_symlink():
            continue
        dest = run_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
    return case_dir


def _default_smoke_command(repo_root: Path) -> list[str]:
    core = _selected_core(repo_root)
    if core == "fv3":
        candidates = [
            repo_root / ".noraa" / "build" / "bin" / "ufs_model",
            repo_root / ".noraa" / "build" / "ufs_model",
            repo_root / "build" / "ufs_model",
        ]
    else:
        candidates = [repo_root / ".noraa" / "build" / "bin" / "mpas_atmosphere"]
    exe = next((p for p in candidates if p.exists()), candidates[0])
    return [str(exe)]


def execute_smoke(
    *,
    repo_root: Path,
    timeout_sec: int = 20,
    command_text: str | None = None,
    require_artifacts: bool = False,
) -> ExecuteResult:
    run_dir = _smoke_exec_dir(repo_root)
    core = _selected_core(repo_root)
    command = (
        shlex.split(command_text, posix=(os.name != "nt"))
        if command_text
        else _default_smoke_command(repo_root)
    )
    if not command_text and core == "mpas":
        _stage_case_into_run_dir(repo_root, run_dir)

    command_record = " ".join(command) + "\n"
    (run_dir / "command.txt").write_text(command_record, encoding="utf-8")
    stdout_path = run_dir / "stdout.txt"
    stderr_path = run_dir / "stderr.txt"
    _remove_stale_output_artifacts(run_dir)

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
            artifacts = _collect_output_artifacts(run_dir)
            if require_artifacts and not artifacts:
                reason = (
                    "Smoke execution command returned success, but no output artifacts were produced "
                    "(expected history*/diag*/restart*/output*.nc)."
                )
                ok = False
            else:
                reason = "Smoke execution command completed successfully."
                ok = True
        elif _looks_like_runtime_ok(proc.stdout or "", proc.stderr or "") or _looks_like_runtime_ok_from_logs(run_dir):
            artifacts = _collect_output_artifacts(run_dir)
            fatal = _fatal_runtime_marker(proc.stdout or "", proc.stderr or "", run_dir)
            if fatal:
                reason = f"Smoke execution reached runtime but failed: {fatal}."
                ok = False
            elif require_artifacts and not artifacts:
                reason = (
                    "Smoke execution reached runtime checks, but produced no output artifacts "
                    "(expected history*/diag*/restart*/output*.nc)."
                )
                ok = False
            else:
                reason = (
                    "Smoke execution reached runtime checks (non-zero exit, but executable launched and "
                    "reported expected runtime input/namelist guidance)."
                )
                ok = True
        else:
            reason = f"Smoke execution failed with return code {proc.returncode}."
            ok = False
            artifacts = _collect_output_artifacts(run_dir)
        returncode: int | None = proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text(exc.stdout or "", encoding="utf-8")
        stderr_path.write_text(exc.stderr or "", encoding="utf-8")
        artifacts = _collect_output_artifacts(run_dir)
        if require_artifacts and not artifacts:
            reason = (
                f"Smoke execution exceeded timeout ({timeout_sec}s) after launch and produced no output artifacts."
            )
            ok = False
        else:
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
        artifacts = ()

    (run_dir / "result.txt").write_text(
        "\n".join(
            [
                f"ok={str(ok).lower()}",
                f"returncode={'' if returncode is None else returncode}",
                f"reason={reason}",
                f"artifacts={','.join(artifacts)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return ExecuteResult(
        ok=ok,
        run_dir=run_dir,
        command=command,
        returncode=returncode,
        reason=reason,
        artifacts=artifacts,
    )
