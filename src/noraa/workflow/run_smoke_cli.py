from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from ..messages import fail, repo_cmd
from . import run_smoke


def _compatibility_label(runtime_compatible: bool) -> str:
    return "runtime-ready" if runtime_compatible else "metadata-only"


def _human_bytes(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "size: n/a"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(num_bytes)
    idx = 0
    while size >= 1024.0 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"size: {int(size)} {units[idx]}"
    return f"size: {size:.1f} {units[idx]}"


def _filter_regtests_prefixes_for_core(options: list[str], core: str) -> list[str]:
    core_norm = (core or "mpas").strip().lower()
    if core_norm not in {"mpas", "fv3"}:
        return options

    def _basename(prefix: str) -> str:
        return prefix.strip().strip("/").split("/")[-1].upper()

    if core_norm == "fv3":
        filtered = [
            opt for opt in options if (_basename(opt) == "FV3" or "FV3" in _basename(opt))
        ]
    else:
        filtered = [
            opt for opt in options if (_basename(opt) == "MPAS" or "MPAS" in _basename(opt))
        ]
    return filtered or options


def _print_fetch_result(
    repo_root: Path,
    source_repo: str,
    source_path: str,
    manifest: Path,
    citation: str | None = None,
) -> None:
    print(f"Source repository: {source_repo}")
    print(f"Source path: {source_path}")
    if citation:
        print(f"Citation: {citation}")
    print(f"Dataset manifest written: {manifest}")
    print(f"Run this command next: {repo_cmd(repo_root, 'run-smoke', 'status')}")


def status(repo_root: Path) -> None:
    checks = run_smoke.collect_status_checks(repo_root)
    report, _ = run_smoke.format_status_report(checks)
    print(report)


def status_short(repo_root: Path) -> None:
    checks = run_smoke.collect_status_checks(repo_root)
    report, _ = run_smoke.format_status_short(checks)
    print(report)


def validate_data(repo_root: Path) -> None:
    ok, detail, action = run_smoke.validate_runtime_data(repo_root)
    if ok:
        print("Runtime dataset validation: OK")
        print(f"Case directory: {detail}")
        return
    print("Runtime dataset validation: NOT OK")
    print(f"Details: {detail}")
    if action:
        print(f"Action required: {action}")


def fetch_scan(
    *,
    repo_root: Path,
    dataset: str | None,
    yes: bool,
    prompt_int: Callable[[str], int],
) -> None:
    candidates = run_smoke.discover_dataset_candidates(repo_root)
    if not candidates:
        fail(
            "No candidate .nc datasets were discovered in official checked-out repos.",
            next_step=(
                f"Try curated data: {repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'official')} "
                f"or local files: {repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/data"
            ),
        )

    selected = None
    if dataset:
        for c in candidates:
            if c.name == dataset:
                selected = c
                break
        if selected is None:
            names = ", ".join(c.name for c in candidates[:8])
            fail(
                f"Dataset '{dataset}' was not found among discovered candidates.",
                next_step=f"Re-run without --dataset to choose interactively. Example candidates: {names}",
            )
    elif yes or len(candidates) == 1:
        selected = candidates[0]
    else:
        print("Discovered dataset candidates:")
        for i, c in enumerate(candidates, start=1):
            source_repo = c.source_repo_url or str(c.source_repo_path)
            print(f"{i}. {c.name}  [source: {source_repo}]")
            print(f"   ic:  {c.ic_file}")
            if c.lbc_file:
                print(f"   lbc: {c.lbc_file}")
        choice = prompt_int("Select dataset number")
        if choice < 1 or choice > len(candidates):
            fail(
                f"Invalid dataset selection: {choice}",
                next_step=repo_cmd(repo_root, "run-smoke", "fetch-data", "scan"),
            )
        selected = candidates[choice - 1]

    source_repo = selected.source_repo_url or str(selected.source_repo_path)
    manifest = run_smoke.fetch_dataset(repo_root=repo_root, candidate=selected)
    _print_fetch_result(repo_root, source_repo, str(selected.ic_file), manifest)


def fetch_official(
    *,
    repo_root: Path,
    dataset: str | None,
    yes: bool,
    prompt_int: Callable[[str], int],
) -> None:
    catalog = run_smoke.official_catalog()
    selected_official = None
    if dataset:
        selected_official = run_smoke.resolve_official_dataset(dataset)
        if selected_official is None:
            ids = ", ".join(ds.dataset_id for ds in catalog)
            fail(
                f"Unknown official dataset id: {dataset}",
                next_step=f"Use one of: {ids}",
            )
    elif yes or len(catalog) == 1:
        selected_official = catalog[0]
    else:
        print("Official dataset options:")
        for i, ds in enumerate(catalog, start=1):
            label = _compatibility_label(ds.runtime_compatible)
            print(f"{i}. {ds.dataset_id}  [source: {ds.source_repo}]  [compat: {label}]")
            print(f"   {ds.description}")
            print(f"   url: {ds.url}")
        choice = prompt_int("Select official dataset number")
        if choice < 1 or choice > len(catalog):
            fail(
                f"Invalid dataset selection: {choice}",
                next_step=repo_cmd(repo_root, "run-smoke", "fetch-data", "official"),
            )
        selected_official = catalog[choice - 1]

    print(f"Selected official dataset: {selected_official.dataset_id} [{_compatibility_label(selected_official.runtime_compatible)}]")
    manifest = run_smoke.fetch_official_bundle(repo_root=repo_root, dataset=selected_official)
    _print_fetch_result(repo_root, selected_official.source_repo, selected_official.url, manifest)
    if not selected_official.runtime_compatible:
        print(
            "NORAA identified: this official dataset is metadata-only for current ufsatm runtime execution."
        )
        print(
            f"Action required: {repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/ufs-runtime-data --dataset ufs_runtime_case"
        )


def fetch_official_ufs(*, repo_root: Path, s3_prefix: str) -> None:
    if shutil.which("aws") is None:
        fail(
            "NORAA identified: aws CLI is required for official-ufs dataset fetch.",
            next_step="python -m pip install -U awscli",
        )

    try:
        manifest = run_smoke.fetch_official_ufs_prefix(
            repo_root=repo_root,
            s3_prefix=s3_prefix,
            aws_bin="aws",
        )
    except Exception as e:
        fail(
            f"HTF dataset fetch failed: {e}",
            next_step=f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'official-ufs')} --s3-prefix <prefix>",
        )

    citation = run_smoke.htf_citation(run_smoke.current_utc_date())
    _print_fetch_result(
        repo_root,
        run_smoke.HTF_REGISTRY_URL,
        f"{run_smoke.HTF_BUCKET}/{s3_prefix.strip().strip('/')}/",
        manifest,
        citation=citation,
    )


def fetch_official_regtests(
    *,
    repo_root: Path,
    s3_prefix: str | None,
    dry_run: bool,
    yes: bool = False,
    catalog_root: str = "input-data-20251015",
    show_size: bool = False,
    max_size_gb: float | None = 5.0,
    prompt_int: Callable[[str], int] | None = None,
) -> None:
    if shutil.which("aws") is None:
        fail(
            "NORAA identified: aws CLI is required for official-regtests dataset fetch.",
            next_step="python -m pip install -U awscli",
        )

    resolved_prefix = (s3_prefix or "").strip().strip("/")
    if not resolved_prefix:
        workflow_core = run_smoke._selected_core(repo_root)
        try:
            options = run_smoke.list_official_regtests_prefixes(
                aws_bin="aws", catalog_root=catalog_root
            )
        except Exception as e:
            fail(
                f"Unable to list official-regtests prefixes: {e}",
                next_step=f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'official-regtests')} --s3-prefix <prefix>",
            )
        if not options:
            fail(
                "No dataset prefixes were discovered in noaa-ufs-regtests-pds.",
                next_step=f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'official-regtests')} --s3-prefix <prefix>",
            )
        filtered_options = _filter_regtests_prefixes_for_core(options, workflow_core)
        if filtered_options != options:
            print(
                f"Filtered official-regtests options for core={workflow_core}: "
                f"{len(filtered_options)} of {len(options)} prefixes shown."
            )
        options = filtered_options
        if yes or len(options) == 1:
            resolved_prefix = options[0]
        else:
            if prompt_int is None:
                fail(
                    "Interactive selection requires a prompt function.",
                    next_step=f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'official-regtests')} --s3-prefix <prefix>",
                )
            print("Official regtests dataset prefix options:")
            for i, opt in enumerate(options, start=1):
                size_label = ""
                if show_size:
                    try:
                        sz = run_smoke.regtests_prefix_size_bytes(
                            s3_prefix=opt, aws_bin="aws"
                        )
                    except Exception:
                        sz = None
                    size_label = f"  [{_human_bytes(sz)}]"
                print(f"{i}. {opt}{size_label}")
            choice = prompt_int("Select official-regtests prefix number")
            if choice < 1 or choice > len(options):
                fail(
                    f"Invalid dataset selection: {choice}",
                    next_step=repo_cmd(repo_root, "run-smoke", "fetch-data", "official-regtests"),
                )
            resolved_prefix = options[choice - 1]

    if dry_run:
        print("Dry run: official-regtests fetch preview")
        print(f"Source repository: {run_smoke.REGTESTS_SOURCE_URL}")
        print(f"Source path: {run_smoke.REGTESTS_BUCKET}/{resolved_prefix}/")
        print("Run without --dry-run to fetch files.")
        return

    size_bytes = None
    try:
        size_bytes = run_smoke.regtests_prefix_size_bytes(
            s3_prefix=resolved_prefix, aws_bin="aws"
        )
    except Exception:
        size_bytes = None
    if size_bytes is not None:
        print(f"Estimated download size: {_human_bytes(size_bytes)}")
    if max_size_gb is not None and max_size_gb > 0 and size_bytes is not None:
        limit_bytes = int(max_size_gb * 1024 * 1024 * 1024)
        if size_bytes > limit_bytes:
            fail(
                (
                    f"Selected prefix is larger than allowed limit "
                    f"({_human_bytes(size_bytes)} > {max_size_gb:.1f} GB)."
                ),
                next_step=(
                    f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'official-regtests')} "
                    f"--s3-prefix {resolved_prefix} --max-size-gb {size_bytes / (1024 * 1024 * 1024):.1f}"
                ),
            )

    try:
        manifest = run_smoke.fetch_official_regtests_prefix(
            repo_root=repo_root,
            s3_prefix=resolved_prefix,
            aws_bin="aws",
        )
    except Exception as e:
        fail(
            f"Regtests dataset fetch failed: {e}",
            next_step=f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'official-regtests')} --s3-prefix <prefix>",
        )

    _print_fetch_result(
        repo_root,
        run_smoke.REGTESTS_SOURCE_URL,
        f"{run_smoke.REGTESTS_BUCKET}/{resolved_prefix}/",
        manifest,
    )


def fetch_local(
    *,
    repo_root: Path,
    local_path: str,
    dataset: str,
    auto_fix_mpas_compat: bool = True,
) -> None:
    p = Path(local_path).expanduser().resolve()
    if not p.exists():
        fail(
            f"Local data path does not exist: {p}",
            next_step=f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/data",
        )
    ok, detail = run_smoke.validate_runtime_case_dir(p) if p.is_dir() else (False, "Local path must be a directory for runtime validation.")
    print(
        f"Local dataset validation before import: "
        f"{'runtime-ready' if ok else 'metadata-only'}"
    )
    print(f"Validation detail: {detail}")
    manifest = run_smoke.fetch_local_dataset(
        repo_root=repo_root,
        local_path=p,
        dataset_name=dataset,
        auto_fix_mpas_compat=auto_fix_mpas_compat,
    )
    _print_fetch_result(repo_root, "user-local", str(p), manifest)
    fixes = run_smoke.local_dataset_compat_fixes(repo_root)
    if fixes:
        print("Applied MPAS compatibility fixes:")
        for fix in fixes:
            print(f"- {fix}")


def fetch_local_dry_run(*, local_path: str) -> None:
    p = Path(local_path).expanduser().resolve()
    if not p.exists():
        fail("Local data path does not exist for dry-run validation.", next_step="Provide an existing --local-path")
    if not p.is_dir():
        fail("Dry-run validation expects a directory path.", next_step="Provide a directory containing runtime case files")
    ok, detail = run_smoke.validate_runtime_case_dir(p)
    print(
        f"Local dataset dry-run validation: "
        f"{'runtime-ready' if ok else 'metadata-only'}"
    )
    print(f"Details: {detail}")


def clean_data(*, repo_root: Path, dataset: str | None) -> None:
    data_root = repo_root / ".noraa" / "runs" / "smoke" / "data"
    manifest = data_root / "dataset.toml"
    if dataset:
        target = data_root / dataset
        if not target.exists():
            fail(
                f"Dataset not found: {target}",
                next_step=repo_cmd(repo_root, "run-smoke", "status"),
            )
        shutil.rmtree(target)
        if manifest.exists() and f'name = "{dataset}"' in manifest.read_text(encoding="utf-8", errors="ignore"):
            manifest.unlink()
        print(f"Removed dataset: {target}")
        return
    if data_root.exists():
        shutil.rmtree(data_root)
    print(f"Removed smoke data root: {data_root}")


def execute(
    *,
    repo_root: Path,
    timeout_sec: int,
    command: str | None,
    require_output: bool,
) -> None:
    checks = run_smoke.collect_status_checks(repo_root)
    _, ready = run_smoke.format_status_report(checks)
    if not ready:
        next_cmd = run_smoke.first_blocking_action(checks) or repo_cmd(
            repo_root, "run-smoke", "status"
        )
        fail(
            "Run-smoke execute is blocked because readiness is not GREEN.",
            next_step=next_cmd,
        )

    result = run_smoke.execute_smoke(
        repo_root=repo_root,
        timeout_sec=timeout_sec,
        command_text=command,
        require_artifacts=require_output,
    )
    print("NORAA run-smoke execution summary:")
    print(f"Run directory: {result.run_dir}")
    print(f"Command: {' '.join(result.command)}")
    print(f"Result: {run_smoke.execution_label(result)}")
    print(f"Details: {result.reason}")
    if result.artifacts:
        print(f"Artifacts: {', '.join(result.artifacts)}")
    elif require_output:
        print("Artifacts: none")
    print(f"Logs: {result.run_dir}")
    if result.ok:
        print(f"Run this command next: {repo_cmd(repo_root, 'run-smoke', 'status')}")
        return
    fail(
        "Run-smoke execute failed.",
        next_step=repo_cmd(repo_root, "run-smoke", "execute"),
        logs=result.run_dir,
    )
