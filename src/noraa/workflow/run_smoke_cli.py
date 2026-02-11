from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from ..messages import fail, repo_cmd
from . import run_smoke


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
            print(f"{i}. {ds.dataset_id}  [source: {ds.source_repo}]")
            print(f"   {ds.description}")
            print(f"   url: {ds.url}")
        choice = prompt_int("Select official dataset number")
        if choice < 1 or choice > len(catalog):
            fail(
                f"Invalid dataset selection: {choice}",
                next_step=repo_cmd(repo_root, "run-smoke", "fetch-data", "official"),
            )
        selected_official = catalog[choice - 1]

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


def fetch_local(*, repo_root: Path, local_path: str, dataset: str) -> None:
    p = Path(local_path).expanduser().resolve()
    if not p.exists():
        fail(
            f"Local data path does not exist: {p}",
            next_step=f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'local')} --local-path /path/to/data",
        )
    manifest = run_smoke.fetch_local_dataset(
        repo_root=repo_root, local_path=p, dataset_name=dataset
    )
    _print_fetch_result(repo_root, "user-local", str(p), manifest)


def execute(*, repo_root: Path, timeout_sec: int, command: str | None) -> None:
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
        repo_root=repo_root, timeout_sec=timeout_sec, command_text=command
    )
    print("NORAA run-smoke execution summary:")
    print(f"Run directory: {result.run_dir}")
    print(f"Command: {' '.join(result.command)}")
    print(f"Result: {run_smoke.execution_label(result)}")
    print(f"Details: {result.reason}")
    print(f"Logs: {result.run_dir}")
    if result.ok:
        print(f"Run this command next: {repo_cmd(repo_root, 'run-smoke', 'status')}")
        return
    fail(
        "Run-smoke execute failed.",
        next_step=repo_cmd(repo_root, "run-smoke", "execute"),
        logs=result.run_dir,
    )
