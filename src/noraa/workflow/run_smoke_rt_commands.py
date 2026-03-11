from __future__ import annotations

import shlex
import tomllib
from pathlib import Path
from typing import Callable

import typer

from ..bootstrap.tasks import bootstrap_deps, bootstrap_esmf
from ..messages import fail, repo_cmd
from ..project import ProjectConfig, write_project
from . import run_smoke, run_smoke_cli


def _is_back_token(value: str) -> bool:
    token = value.strip().lower()
    return token in {"b", "back", "go back", "goback", "previous"}


def _looks_like_menu_token(value: str) -> bool:
    return value.strip() in {"1", "2"}


def _parse_menu_choice(raw: str, valid: set[int]) -> int | None:
    text = raw.strip()
    if not text:
        return None
    if _is_back_token(text):
        return -1
    if not text.isdigit():
        return None
    value = int(text)
    if value not in valid:
        return None
    return value


def _write_fv3_launcher_script(*, repo_root: Path, case_dir: Path, exe_path: Path) -> Path:
    launcher_dir = repo_root / ".noraa" / "runs" / "smoke"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    launcher_path = launcher_dir / "fv3_launcher.sh"
    launcher_text = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        f"CASE_DIR={shlex.quote(str(case_dir))}\n"
        f"EXE={shlex.quote(str(exe_path))}\n\n"
        'cp -a "$CASE_DIR"/. .\n'
        'exec "$EXE"\n'
    )
    launcher_path.write_text(launcher_text, encoding="utf-8")
    launcher_path.chmod(0o755)
    return launcher_path


def _dataset_bundle_dir(repo_root: Path) -> Path | None:
    manifest = repo_root / ".noraa" / "runs" / "smoke" / "data" / "dataset.toml"
    if not manifest.exists():
        return None
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None
    dataset = data.get("dataset")
    if not isinstance(dataset, dict):
        return None
    bundle_dir = dataset.get("bundle_dir")
    if not isinstance(bundle_dir, str) or not bundle_dir.strip():
        return None
    bundle_path = repo_root / ".noraa" / "runs" / "smoke" / "data" / bundle_dir
    return bundle_path if bundle_path.exists() and bundle_path.is_dir() else None


def _resolve_selected_core(
    *,
    cfg: ProjectConfig,
    core: str | None,
    yes: bool,
    normalize_core: Callable[[str], str],
) -> str:
    selected_core = normalize_core(core or cfg.core)
    if core is None and not yes:
        print("Select core for this guided run:")
        print(f"1) mpas{' (current project default)' if cfg.core == 'mpas' else ''}")
        print(f"2) fv3{' (current project default)' if cfg.core == 'fv3' else ''}")
        choice = typer.prompt(
            "Choose core number",
            type=int,
            default=(1 if cfg.core == "mpas" else 2),
        )
        if choice not in {1, 2}:
            fail("Invalid core selection.", next_step="Use --core mpas or --core fv3")
        selected_core = "mpas" if choice == 1 else "fv3"
    return selected_core


def _validate_case_dir_for_core(*, core: str, case_dir: Path) -> tuple[bool, str]:
    if core == "fv3":
        return run_smoke.validate_fv3_runtime_case_dir(case_dir)
    return run_smoke.validate_runtime_case_dir(case_dir)


def register_run_smoke_and_rt_commands(
    *,
    run_smoke_app: typer.Typer,
    run_smoke_fetch_app: typer.Typer,
    rt_app: typer.Typer,
    target_repo: Callable[[str], Path],
    require_project: Callable[[Path], ProjectConfig],
    normalize_core: Callable[[str], str],
    verify_runner: Callable[[Path, str], None],
) -> None:
    @rt_app.command("validate-case")
    def rt_validate_case(
        repo: str = typer.Option(".", "--repo"),
        core: str = typer.Option("mpas", "--core", help="Core to validate (mpas or fv3)."),
        case_dir: str = typer.Option(..., "--case-dir", help="Runtime case directory to validate."),
    ):
        """Validate a local runtime case directory for MPAS or FV3 before execute."""
        repo_root = target_repo(repo)
        require_project(repo_root)
        selected_core = normalize_core(core)
        path = Path(case_dir).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            fail(
                f"Case directory does not exist: {path}",
                next_step="Provide a valid --case-dir path.",
            )
        ok, detail = _validate_case_dir_for_core(core=selected_core, case_dir=path)
        if ok:
            print(f"Runtime case validation: OK [{selected_core}]")
            print(f"Case directory: {detail}")
        else:
            print(f"Runtime case validation: NOT OK [{selected_core}]")
            print(f"Details: {detail}")
            if selected_core == "fv3":
                print("Action required: provide an RT-style FV3 runtime case with input.nml/model_configure/ufs.configure.")
            else:
                print("Action required: provide a UFS-compatible MPAS runtime case with namelist.atmosphere/streams.atmosphere.")

    @rt_app.command("advanced-guide")
    def rt_advanced_guide(
        repo: str = typer.Option(".", "--repo"),
        core: str | None = typer.Option(
            None,
            "--core",
            help="Core to guide (mpas or fv3). If omitted, NORAA will ask.",
        ),
        platform: str = typer.Option(
            "linux",
            "--platform",
            help="Target platform for command hints (linux, jet, hera).",
        ),
        yes: bool = typer.Option(False, "--yes", help="Auto-accept recommended guided steps."),
        timeout_sec: int = typer.Option(120, "--timeout-sec", help="Execution timeout for guided smoke run."),
    ):
        """
        Advanced RT-style walkthrough with explicit runtime case validation and platform command hints.
        """
        repo_root = target_repo(repo)
        cfg = require_project(repo_root)
        selected_core = _resolve_selected_core(
            cfg=cfg,
            core=core,
            yes=yes,
            normalize_core=normalize_core,
        )
        plat = platform.strip().lower()
        if plat not in {"linux", "jet", "hera"}:
            fail("Unsupported platform option.", next_step="Use --platform linux|jet|hera")

        print("NORAA Advanced RT Guide")
        print(f"Repository: {repo_root}")
        print(f"Selected core: {selected_core}")
        print(f"Platform profile: {plat}")
        print("Goal: execute a validated runtime case and produce artifacts.")
        print("Phase 1/5: Environment and source readiness.")
        if plat in {"jet", "hera"}:
            print("Suggested module stack (adjust to your site policies):")
            print("module use /contrib/spack-stack/spack-stack-1.6.0/envs/unified-env-rocky8/install/modulefiles/Core")
            print("module load stack-intel/2021.5.0 stack-intel-oneapi-mpi/2021.5.1 jedi-mpas-env/1.0.0")
        print("Ensure submodules are initialized:")
        print("git submodule update --init --recursive")

        verify_cmd = f"{repo_cmd(repo_root, 'verify')} --core {selected_core}"
        do_verify = yes or typer.confirm(
            f"Phase 2/5: Run verify now? ({verify_cmd})",
            default=True,
        )
        if do_verify:
            verify_runner(repo_root, selected_core)
        else:
            print(f"Run this command next: {verify_cmd}")

        print("Phase 3/5: Acquire runtime case and validate before execute.")
        case_dir: Path | None = None
        if selected_core == "fv3":
            print("1) Use local RT-style FV3 case directory")
            print("2) Fetch from official UFS HTF bucket")
            while True:
                if yes:
                    source = 1
                else:
                    raw_source = typer.prompt(
                        "Choose source number (or type 'back')",
                        type=str,
                        default="1",
                    )
                    parsed_source = _parse_menu_choice(raw_source, {1, 2})
                    if parsed_source is None:
                        print("Invalid source selection. Enter 1 or 2.")
                        continue
                    if parsed_source == -1:
                        print("Already at source selection. Enter 1 or 2.")
                        continue
                    source = parsed_source
                break
            if source == 2:
                s3_prefix = typer.prompt(
                    "Enter HTF S3 prefix under noaa-ufs-htf-pds (example: develop-20250530/<case-path>, or type 'back')",
                    type=str,
                ).strip()
                if _is_back_token(s3_prefix):
                    print("Returning to source selection.")
                    return
                run_smoke_cli.fetch_official_ufs(repo_root=repo_root, s3_prefix=s3_prefix)
                bundle = _dataset_bundle_dir(repo_root)
                if bundle is None:
                    fail(
                        "Could not resolve fetched bundle directory from dataset manifest.",
                        next_step=f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'official-ufs')} --s3-prefix <prefix>",
                )
                case_dir = bundle
            else:
                while True:
                    raw_case = typer.prompt(
                        "Enter local RT-style case directory path (or type 'back')",
                        type=str,
                    ).strip()
                    if _is_back_token(raw_case):
                        print("Returning to source selection.")
                        return
                    if _looks_like_menu_token(raw_case):
                        print("Input looks like a menu token. Enter a path or type 'back'.")
                        continue
                    candidate_case = Path(raw_case).expanduser().resolve()
                    if not candidate_case.exists() or not candidate_case.is_dir():
                        print(f"Case directory does not exist: {candidate_case}. Try again or type 'back'.")
                        continue
                    case_dir = candidate_case
                    break
        else:
            print("1) Fetch from official-regtests catalog (Recommended)")
            print("2) Use local MPAS runtime case directory")
            while True:
                if yes:
                    source = 1
                else:
                    raw_source = typer.prompt(
                        "Choose source number (or type 'back')",
                        type=str,
                        default="1",
                    )
                    parsed_source = _parse_menu_choice(raw_source, {1, 2})
                    if parsed_source is None:
                        print("Invalid source selection. Enter 1 or 2.")
                        continue
                    if parsed_source == -1:
                        print("Already at source selection. Enter 1 or 2.")
                        continue
                    source = parsed_source
                break
            if source == 1:
                run_smoke_cli.fetch_official_regtests(
                    repo_root=repo_root,
                    s3_prefix=None,
                    dry_run=False,
                    yes=yes,
                    catalog_root="input-data-20251015",
                    show_size=True,
                    max_size_gb=5.0,
                    prompt_int=lambda msg: typer.prompt(msg, type=int),
                )
                bundle = _dataset_bundle_dir(repo_root)
                if bundle is not None:
                    case_dir = bundle
            else:
                local_path = typer.prompt(
                    "Enter local MPAS runtime case directory path (or type 'back')",
                    type=str,
                ).strip()
                if _is_back_token(local_path):
                    print("Returning to source selection.")
                    return
                dataset = "ufs_runtime_case"
                if not yes:
                    dataset_raw = typer.prompt(
                        "Dataset name to register (or type 'back')",
                        type=str,
                        default="ufs_runtime_case",
                    ).strip()
                    if _is_back_token(dataset_raw):
                        print("Returning to source selection.")
                        return
                    dataset = dataset_raw
                run_smoke_cli.fetch_local(
                    repo_root=repo_root,
                    local_path=local_path,
                    dataset=dataset,
                    auto_fix_mpas_compat=True,
                )
                bundle = _dataset_bundle_dir(repo_root)
                if bundle is not None:
                    case_dir = bundle

        if case_dir is not None:
            ok, detail = _validate_case_dir_for_core(core=selected_core, case_dir=case_dir)
            if ok:
                print(f"Runtime case validation: OK [{selected_core}] [{detail}]")
            else:
                fail(
                    f"Runtime case validation failed for core={selected_core}: {detail}",
                    next_step=f"{repo_cmd(repo_root, 'rt', 'validate-case')} --core {selected_core} --case-dir {case_dir}",
                )
        else:
            print("Runtime case path could not be resolved automatically. Use rt validate-case once you have the directory.")

        print("Phase 4/5: Re-check readiness status.")
        run_smoke_cli.status(repo_root)
        checks = run_smoke.collect_status_checks(repo_root)
        _report, ready = run_smoke.format_status_report(checks)
        if not ready:
            print("Blocking checks remain. Resolve RED items before execution.")
            return

        run_now = yes or typer.confirm("Phase 5/5: Run execute now?", default=True)
        if not run_now:
            print(
                f"Run this command next: {repo_cmd(repo_root, 'run-smoke', 'execute')} --timeout-sec {timeout_sec}"
            )
            return
        command_override = None
        if selected_core == "fv3":
            if case_dir is None:
                fail(
                    "Cannot build FV3 launcher: case directory was not resolved.",
                    next_step="Provide a valid RT-style case and rerun advanced-guide.",
                )
            while True:
                exe_raw = typer.prompt(
                    "Enter executable path (ufs_model, or type 'back')",
                    type=str,
                ).strip()
                if _is_back_token(exe_raw):
                    print("Execution cancelled. Re-run advanced-guide when ready.")
                    return
                if _looks_like_menu_token(exe_raw):
                    print("Input looks like a menu token. Enter an executable path or type 'back'.")
                    continue
                exe_path = Path(exe_raw).expanduser().resolve()
                if not exe_path.exists() or not exe_path.is_file():
                    print(f"Executable path does not exist: {exe_path}. Try again or type 'back'.")
                    continue
                break
            launcher = _write_fv3_launcher_script(
                repo_root=repo_root,
                case_dir=case_dir,
                exe_path=exe_path,
            )
            print(f"Generated launcher script: {launcher}")
            command_override = str(launcher)

        run_smoke_cli.execute(
            repo_root=repo_root,
            timeout_sec=timeout_sec,
            command=command_override,
            require_output=True,
        )

    @run_smoke_app.command("status")
    def run_smoke_status(
        repo: str = typer.Option(".", "--repo"),
        short: bool = typer.Option(False, "--short", help="Print one-line readiness summary."),
    ):
        """Report readiness for optional run-smoke workflows with RED/GREEN checks."""
        repo_root = target_repo(repo)
        if short:
            run_smoke_cli.status_short(repo_root)
        else:
            run_smoke_cli.status(repo_root)

    @run_smoke_app.command("validate-data")
    def run_smoke_validate_data(repo: str = typer.Option(".", "--repo")):
        """Validate runtime dataset compatibility and list the first blocking issue/action."""
        repo_root = target_repo(repo)
        run_smoke_cli.validate_data(repo_root)

    @run_smoke_fetch_app.command("scan")
    def run_smoke_fetch_data_scan(
        repo: str = typer.Option(".", "--repo"),
        dataset: str = typer.Option(
            None,
            "--dataset",
            help="Dataset name from discovered repo candidates.",
        ),
        yes: bool = typer.Option(False, "--yes", help="Auto-select first candidate."),
    ):
        """Discover candidate `.nc` data in checked-out repos and register one dataset."""
        repo_root = target_repo(repo)
        require_project(repo_root)
        run_smoke_cli.fetch_scan(
            repo_root=repo_root,
            dataset=dataset,
            yes=yes,
            prompt_int=lambda msg: typer.prompt(msg, type=int),
        )

    @run_smoke_fetch_app.command("official")
    def run_smoke_fetch_data_official(
        repo: str = typer.Option(".", "--repo"),
        dataset: str = typer.Option(
            None,
            "--dataset",
            help="Official curated dataset id.",
        ),
        yes: bool = typer.Option(False, "--yes", help="Auto-select first candidate."),
    ):
        """Select from curated official MPAS test-case bundles and register metadata."""
        repo_root = target_repo(repo)
        require_project(repo_root)
        run_smoke_cli.fetch_official(
            repo_root=repo_root,
            dataset=dataset,
            yes=yes,
            prompt_int=lambda msg: typer.prompt(msg, type=int),
        )

    @run_smoke_fetch_app.command("official-ufs")
    def run_smoke_fetch_data_official_ufs(
        repo: str = typer.Option(".", "--repo"),
        s3_prefix: str = typer.Option(
            ...,
            "--s3-prefix",
            help="HTF S3 prefix under noaa-ufs-htf-pds (example: develop-20250530/<case-path>).",
        ),
    ):
        """Fetch UFS/HTF case data from noaa-ufs-htf-pds and record required citation."""
        repo_root = target_repo(repo)
        require_project(repo_root)
        run_smoke_cli.fetch_official_ufs(repo_root=repo_root, s3_prefix=s3_prefix)

    @run_smoke_fetch_app.command("official-regtests")
    def run_smoke_fetch_data_official_regtests(
        repo: str = typer.Option(".", "--repo"),
        s3_prefix: str = typer.Option(
            None,
            "--s3-prefix",
            help="Optional S3 prefix under noaa-ufs-regtests-pds (example: input-data-20251015/MPAS). If omitted, NORAA will list catalog options.",
        ),
        catalog_root: str = typer.Option(
            "input-data-20251015",
            "--catalog-root",
            help="Catalog root prefix to query when --s3-prefix is omitted.",
        ),
        show_size: bool = typer.Option(
            False,
            "--show-size",
            help="Show per-prefix size estimates when listing catalog options (can be slower).",
        ),
        max_size_gb: float = typer.Option(
            5.0,
            "--max-size-gb",
            help="Refuse download if estimated prefix size is larger than this limit. Set <=0 to disable.",
        ),
        yes: bool = typer.Option(
            False, "--yes", help="Auto-select first discovered prefix when --s3-prefix is omitted."
        ),
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview source path without downloading."),
    ):
        """Fetch candidate case data from noaa-ufs-regtests-pds and validate compatibility via status checks."""
        repo_root = target_repo(repo)
        require_project(repo_root)
        run_smoke_cli.fetch_official_regtests(
            repo_root=repo_root,
            s3_prefix=s3_prefix,
            dry_run=dry_run,
            yes=yes,
            catalog_root=catalog_root,
            show_size=show_size,
            max_size_gb=(None if max_size_gb <= 0 else max_size_gb),
            prompt_int=lambda msg: typer.prompt(msg, type=int),
        )

    @run_smoke_fetch_app.command("local")
    def run_smoke_fetch_data_local(
        repo: str = typer.Option(".", "--repo"),
        local_path: str = typer.Option(
            ...,
            "--local-path",
            help="Directory or file path containing user dataset files.",
        ),
        dataset: str = typer.Option(
            "local_user_data",
            "--dataset",
            help="Dataset name to store under .noraa/runs/smoke/data.",
        ),
        auto_fix_mpas_compat: bool = typer.Option(
            True,
            "--auto-fix-mpas-compat/--no-auto-fix-mpas-compat",
            help="Apply conservative local MPAS compatibility fixes during import.",
        ),
        dry_run: bool = typer.Option(False, "--dry-run", help="Validate local case files without importing."),
    ):
        """Register user-provided local dataset files under `.noraa/runs/smoke/data`."""
        repo_root = target_repo(repo)
        require_project(repo_root)
        if dry_run:
            run_smoke_cli.fetch_local_dry_run(local_path=local_path)
            return
        run_smoke_cli.fetch_local(
            repo_root=repo_root,
            local_path=local_path,
            dataset=dataset,
            auto_fix_mpas_compat=auto_fix_mpas_compat,
        )

    @run_smoke_fetch_app.command("clean-data")
    def run_smoke_fetch_data_clean(
        repo: str = typer.Option(".", "--repo"),
        dataset: str = typer.Option(None, "--dataset", help="Optional dataset directory name to remove."),
    ):
        """Remove one dataset directory or all smoke data under .noraa/runs/smoke/data."""
        repo_root = target_repo(repo)
        require_project(repo_root)
        run_smoke_cli.clean_data(repo_root=repo_root, dataset=dataset)

    @run_smoke_app.command("execute")
    def run_smoke_execute(
        repo: str = typer.Option(".", "--repo"),
        timeout_sec: int = typer.Option(20, "--timeout-sec"),
        command: str = typer.Option(
            None,
            "--command",
            help="Optional override command to run in the smoke execution directory.",
        ),
        require_output: bool = typer.Option(
            True,
            "--require-output/--allow-no-output",
            help="Require netCDF output artifacts (history/diag/restart/output) for success.",
        ),
    ):
        """Run a short structured smoke execution probe after readiness is GREEN."""
        repo_root = target_repo(repo)
        require_project(repo_root)
        run_smoke_cli.execute(
            repo_root=repo_root,
            timeout_sec=timeout_sec,
            command=command,
            require_output=require_output,
        )

    @rt_app.command("guide")
    def rt_guide(
        repo: str = typer.Option(".", "--repo"),
        core: str | None = typer.Option(
            None,
            "--core",
            help="Core to guide (mpas or fv3). If omitted, NORAA will ask.",
        ),
        yes: bool = typer.Option(False, "--yes", help="Auto-accept recommended guided steps."),
        timeout_sec: int = typer.Option(60, "--timeout-sec", help="Execution timeout for guided smoke run."),
    ):
        """
        Walk the user through an RT-style path to a real runtime product.

        This command guides: verify -> runtime data source selection -> readiness status -> execute.
        """
        repo_root = target_repo(repo)
        cfg = require_project(repo_root)
        selected_core = _resolve_selected_core(
            cfg=cfg,
            core=core,
            yes=yes,
            normalize_core=normalize_core,
        )

        if selected_core != cfg.core:
            update_core = yes or typer.confirm(
                f"Update project default core from {cfg.core} to {selected_core}?",
                default=True,
            )
            if update_core:
                cfg.core = selected_core
                cfg.verify_script = (
                    "scripts/verify_fv3_smoke.sh"
                    if selected_core == "fv3"
                    else "scripts/verify_mpas_smoke.sh"
                )
                write_project(repo_root, cfg)
                print(f"Updated project core default to: {selected_core}")
            else:
                print(
                    "Continuing without updating project core. "
                    "Note: run-smoke status/execute behavior follows project core."
                )

        print("NORAA RT Guide")
        print(f"Repository: {repo_root}")
        print(f"Selected core: {selected_core}")
        print("Goal: reach an end-to-end runtime path with real output artifacts.")

        verify_cmd = f"{repo_cmd(repo_root, 'verify')} --core {selected_core}"
        do_verify = yes or typer.confirm(
            f"Step 1/4: Run verify now? ({verify_cmd})",
            default=True,
        )
        if do_verify:
            verify_runner(repo_root, selected_core)
        else:
            print(f"Run this command next: {verify_cmd}")

        if selected_core == "mpas":
            print("Step 2/4: Select runtime data source for MPAS.")
            while True:
                source = 1
                if not yes:
                    print("1) official-regtests (Recommended)")
                    print("2) local runtime directory")
                    raw_choice = typer.prompt(
                        "Choose data source number (or type 'back')",
                        type=str,
                        default="1",
                    )
                    parsed = _parse_menu_choice(raw_choice, {1, 2})
                    if parsed is None:
                        print("Invalid selection. Enter 1 or 2.")
                        continue
                    if parsed == -1:
                        print("Already at the source selection step. Enter 1 or 2.")
                        continue
                    source = parsed

                if source == 2:
                    local_path_raw = typer.prompt(
                        "Enter local runtime dataset directory path (or type 'back')",
                        type=str,
                    )
                    if _is_back_token(local_path_raw):
                        continue
                    if yes:
                        dataset = "ufs_runtime_case"
                    else:
                        dataset_raw = typer.prompt(
                            "Dataset name to register (or type 'back')",
                            type=str,
                            default="ufs_runtime_case",
                        )
                        if _is_back_token(dataset_raw):
                            continue
                        dataset = dataset_raw
                    run_smoke_cli.fetch_local(
                        repo_root=repo_root,
                        local_path=local_path_raw,
                        dataset=dataset,
                        auto_fix_mpas_compat=True,
                    )
                    break

                run_smoke_cli.fetch_official_regtests(
                    repo_root=repo_root,
                    s3_prefix=None,
                    dry_run=False,
                    yes=yes,
                    catalog_root="input-data-20251015",
                    show_size=True,
                    max_size_gb=5.0,
                    prompt_int=lambda msg: typer.prompt(msg, type=int),
                )
                break
        else:
            print(
                "Step 2/4: For FV3, prepare a valid UFS runtime directory (RT-style) and executable command."
            )

        print("Step 3/4: Checking readiness status.")
        run_smoke_cli.status(repo_root)
        checks = run_smoke.collect_status_checks(repo_root)
        _report, ready = run_smoke.format_status_report(checks)
        if not ready:
            auto_fix = yes or typer.confirm(
                "Blockers detected. Attempt automatic fix for the first blocking item now?",
                default=True,
            )
            if auto_fix:
                next_action = run_smoke.first_blocking_action(checks)
                if next_action == f"{repo_cmd(repo_root, 'verify')} --core {selected_core}":
                    print(f"NORAA auto-fix: running verify for core={selected_core}.")
                    verify_runner(repo_root, selected_core)
                elif next_action == repo_cmd(repo_root, "bootstrap", "esmf"):
                    print("NORAA auto-fix: bootstrapping ESMF.")
                    bootstrap_esmf(repo_root, "v8.6.1")
                elif next_action == repo_cmd(repo_root, "bootstrap", "deps"):
                    print("NORAA auto-fix: bootstrapping deps.")
                    bootstrap_deps(repo_root)
                else:
                    print("NORAA auto-fix: no safe automatic handler for current blocker.")
                print("Re-checking readiness status after auto-fix attempt.")
                run_smoke_cli.status(repo_root)

        run_now = yes or typer.confirm("Step 4/4: Run execute now?", default=True)
        if not run_now:
            if selected_core == "fv3":
                print(
                    f"Run this command next: {repo_cmd(repo_root, 'run-smoke', 'execute')} "
                    '--command "<ufs_runtime_command>" --timeout-sec 60'
                )
            else:
                print(
                    f"Run this command next: {repo_cmd(repo_root, 'run-smoke', 'execute')} --timeout-sec {timeout_sec}"
                )
            return

        command_override = None
        if selected_core == "fv3":
            while command_override is None:
                print("Step 4a/4: Choose FV3 runtime launch mode.")
                print("1) Provide my own runtime command (Recommended)")
                print("2) Guided setup (local case dir or official UFS bucket prefix)")
                if yes:
                    mode = 1
                else:
                    raw_mode = typer.prompt(
                        "Choose launch mode number (or type 'back')",
                        type=str,
                        default="1",
                    )
                    parsed_mode = _parse_menu_choice(raw_mode, {1, 2})
                    if parsed_mode is None:
                        print("Invalid launch mode selection. Enter 1 or 2.")
                        continue
                    if parsed_mode == -1:
                        print("Already at launch mode selection. Enter 1 or 2.")
                        continue
                    mode = parsed_mode

                if mode == 1:
                    prompt_msg = (
                        "Enter command to launch the FV3/UFS runtime "
                        "(from staged run context, or type 'back')"
                    )
                    entered = typer.prompt(prompt_msg, type=str).strip()
                    if _is_back_token(entered):
                        continue
                    if entered in {"1", "2"}:
                        print(
                            "Input looks like a menu selection, not a runtime command. "
                            "Use a real command or type 'back'."
                        )
                        continue
                    command_override = entered
                    continue

                print("Guided setup source:")
                print("1) Use local RT-style case directory")
                print("2) Fetch from official UFS HTF bucket (requires aws CLI)")
                print("3) I don't have a case/prefix yet (show me how)")
                if yes:
                    source_mode = 1
                else:
                    raw_source = typer.prompt(
                        "Choose source number (or type 'back')",
                        type=str,
                        default="1",
                    )
                    parsed_source = _parse_menu_choice(raw_source, {1, 2, 3})
                    if parsed_source is None:
                        print("Invalid source selection. Enter 1, 2, or 3.")
                        continue
                    if parsed_source == -1:
                        continue
                    source_mode = parsed_source

                if source_mode == 3:
                    print("No problem. Use the advanced walkthrough to acquire and validate a runtime case first:")
                    print(
                        f"Run this command next: {repo_cmd(repo_root, 'rt', 'advanced-guide')} "
                        f"--core {selected_core} --platform linux"
                    )
                    print(
                        f"Or fetch directly: {repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'official-ufs')} "
                        "--s3-prefix <develop-YYYYMMDD/case-path>"
                    )
                    return

                if source_mode == 2:
                    s3_prefix = typer.prompt(
                        "Enter HTF S3 prefix under noaa-ufs-htf-pds (example: develop-20250530/<case-path>, or type 'back')",
                        type=str,
                    ).strip()
                    if _is_back_token(s3_prefix):
                        continue
                    run_smoke_cli.fetch_official_ufs(repo_root=repo_root, s3_prefix=s3_prefix)
                    bundle_dir = _dataset_bundle_dir(repo_root)
                    if bundle_dir is None:
                        fail(
                            "Could not resolve fetched bundle directory from dataset manifest.",
                            next_step=(
                                f"{repo_cmd(repo_root, 'run-smoke', 'fetch-data', 'official-ufs')} "
                                "--s3-prefix <prefix>"
                            ),
                        )
                    case_dir = bundle_dir
                    print(f"Using fetched case directory: {case_dir}")
                else:
                    while True:
                        local_case_raw = typer.prompt(
                            "Enter local RT-style case directory path (or type 'back')",
                            type=str,
                        ).strip()
                        if _is_back_token(local_case_raw):
                            case_dir = None
                            break
                        if _looks_like_menu_token(local_case_raw):
                            print(
                                "Input looks like a menu selection, not a path. "
                                "Provide a case directory path or type 'back'."
                            )
                            continue
                        candidate = Path(local_case_raw).expanduser().resolve()
                        if not candidate.exists() or not candidate.is_dir():
                            print(
                                f"Case directory does not exist: {candidate}. "
                                "Try again or type 'back'."
                            )
                            continue
                        case_dir = candidate
                        break
                    if case_dir is None:
                        continue

                while True:
                    exe_raw = typer.prompt(
                        "Enter executable path (ufs_model, or type 'back')",
                        type=str,
                    ).strip()
                    if _is_back_token(exe_raw):
                        case_dir = None
                        break
                    if _looks_like_menu_token(exe_raw):
                        print(
                            "Input looks like a menu selection, not an executable path. "
                            "Provide a file path or type 'back'."
                        )
                        continue
                    exe_path = Path(exe_raw).expanduser().resolve()
                    if not exe_path.exists() or not exe_path.is_file():
                        print(
                            f"Executable path does not exist: {exe_path}. "
                            "Try again or type 'back'."
                        )
                        continue
                    break
                if case_dir is None:
                    continue

                launcher = _write_fv3_launcher_script(
                    repo_root=repo_root,
                    case_dir=case_dir,
                    exe_path=exe_path,
                )
                print(f"Generated launcher script: {launcher}")
                command_override = str(launcher)

        run_smoke_cli.execute(
            repo_root=repo_root,
            timeout_sec=timeout_sec,
            command=command_override,
            require_output=True,
        )
