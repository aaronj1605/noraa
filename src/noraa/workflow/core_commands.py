from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

import typer

from ..agent.diagnose import diagnose_log
from ..bootstrap.tasks import bootstrap_deps, bootstrap_esmf
from ..buildsystem.configure import cmake_fallback_core
from ..buildsystem.env import build_env
from ..buildsystem.paths import (
    detect_verify_script,
    resolve_deps_prefix,
    resolve_esmf_mkfile,
)
from ..fallbacks.fv3 import (
    _apply_fv3_external_sst_fallback,
    _apply_fv3_fms_r8_fallback,
    _apply_fv3_fms_required_fallback,
    _apply_fv3_fv_dynamics_kind_fix,
    _apply_fv3_stochastic_wrapper_stub,
    _apply_fv3_stochy_pattern_fallback,
    _apply_fv3_top_level_dependency_guards,
    _apply_fv3_update_ca_fallback,
)
from ..messages import fail, repo_cmd
from ..project import ProjectConfig, get_origin_url, load_project, write_project
from ..snapshot import write_env_snapshot, write_tool_snapshot
from ..util import log_dir, run_streamed, safe_check_output
from ..validate import validate_core_success
from . import guided_build, preflight


def register_core_commands(
    *,
    app: typer.Typer,
    target_repo: Callable[[str], Path],
    require_project: Callable[[Path], ProjectConfig],
    normalize_core: Callable[[str], str],
) -> tuple[Callable[[Path, str], None], dict[str, Callable]]:
    def _cmake_version() -> tuple[int, int, int] | None:
        return preflight.cmake_version()

    def _verify_preflight_issues(
        repo_root: Path,
        *,
        deps_prefix: str | None,
        esmf_mkfile: str | None,
        using_verify_script: bool,
        core: str,
    ) -> list[tuple[str, str]]:
        return preflight.verify_preflight_issues(
            repo_root,
            deps_prefix=deps_prefix,
            esmf_mkfile=esmf_mkfile,
            using_verify_script=using_verify_script,
            core=core,
            cmake_version_fn=_cmake_version,
        )

    def _format_preflight_summary(issues: list[tuple[str, str]]) -> str:
        return preflight.format_preflight_summary(issues)

    def _run_verify(
        *,
        repo_root: Path,
        core: str | None,
        deps_prefix: str | None,
        esmf_mkfile: str | None,
        clean: bool,
        preflight_only: bool,
        fv3_fms_r8_fallback: bool,
    ) -> None:
        cfg = require_project(repo_root)
        selected_core = normalize_core(core or cfg.core)
        resolved_deps = resolve_deps_prefix(repo_root, deps_prefix)
        if selected_core == "fv3" and fv3_fms_r8_fallback:
            patched_r8 = _apply_fv3_fms_r8_fallback(repo_root, resolved_deps)
            if patched_r8:
                print(
                    "Applied local FV3 fallback: switched FMS component requirement from R4 to R8 "
                    "(fms_r4 not found in deps)."
                )
            else:
                print("FV3 fallback check: no R4->R8 patch needed.")
            if _apply_fv3_fms_required_fallback(repo_root):
                print(
                    "Applied local FV3 fallback: relaxed FMS component requirement to plain REQUIRED."
                )
            if _apply_fv3_top_level_dependency_guards(repo_root):
                print("Applied local FV3 fallback: guarded optional top-level FV3 dependencies.")
            if _apply_fv3_external_sst_fallback(repo_root):
                print(
                    "Applied local FV3 fallback: patched external_sst for newer FMS amip_interp exports."
                )
            if _apply_fv3_stochastic_wrapper_stub(repo_root):
                print(
                    "Applied local FV3 fallback: replaced stochastic_physics_wrapper with no-op stub."
                )
            if _apply_fv3_update_ca_fallback(repo_root):
                print("Applied local FV3 fallback: disabled update_ca restart hooks.")
            if _apply_fv3_stochy_pattern_fallback(repo_root):
                print("Applied local FV3 fallback: disabled get_stochy_pattern restart hooks.")
            if _apply_fv3_fv_dynamics_kind_fix(repo_root):
                print(
                    "Applied local FV3 fallback: fixed fv_dynamics pointer kinds for GFDL interstitial."
                )
        script = Path(cfg.verify_script) if cfg.verify_script else None
        if selected_core == "fv3":
            script = None
            print("FV3 verify mode: using NORAA CMake fallback path.")
        elif not script or not script.exists():
            script = detect_verify_script(repo_root, selected_core)
        issues = _verify_preflight_issues(
            repo_root,
            deps_prefix=resolved_deps,
            esmf_mkfile=esmf_mkfile,
            using_verify_script=bool(script and script.exists()),
            core=selected_core,
        )
        if preflight_only:
            if issues:
                raise SystemExit(_format_preflight_summary(issues))
            print("Preflight OK. No blocking issues found.")
            return
        if issues:
            raise SystemExit(_format_preflight_summary(issues))
        out = log_dir(repo_root, "verify")

        resolved_esmf = resolve_esmf_mkfile(repo_root, resolved_deps, esmf_mkfile)
        env = build_env(resolved_deps, resolved_esmf)

        write_env_snapshot(out, env)
        write_tool_snapshot(out, env)

        if script and script.exists():
            if clean:
                safe_check_output(["bash", "-lc", "rm -rf .noraa/build"], cwd=repo_root, env=env)
            rc = run_streamed(["bash", str(script)], repo_root, out, env)
        else:
            rc = cmake_fallback_core(
                repo_root=repo_root,
                out=out,
                env=env,
                clean=clean,
                deps_prefix=resolved_deps,
                esmf_mkfile=resolved_esmf,
                python_executable=sys.executable,
                core=selected_core,
            )

        (out / "exit_code.txt").write_text(f"{rc}\n")
        v = validate_core_success(repo_root, resolved_deps, out, selected_core)
        (out / "postcheck.txt").write_text(f"ok={v.ok}\nreason={v.reason}\n")

        if rc != 0 or not v.ok:
            code, msg, _, _ = diagnose_log(
                out,
                repo_root,
                deps_prefix=resolved_deps,
                esmf_mkfile=resolved_esmf,
                core=selected_core,
            )
            (out / "diagnosis.txt").write_text(msg)
            print(msg, end="")
            print(f"\nRun this command next: noraa diagnose --repo {repo_root} --log-dir {out}")
            raise SystemExit(code)

        print(f"VERIFY PASSED. Logs: {out}")

    @app.command()
    def init(
        repo: str = typer.Option(".", "--repo"),
        force: bool = typer.Option(False, "--force"),
        core: str = typer.Option(
            "mpas",
            "--core",
            help="Atmospheric dynamical core workflow to configure (mpas or fv3).",
        ),
        upstream_url: str = typer.Option(
            "https://github.com/NOAA-EMC/ufsatm.git", "--upstream-url"
        ),
    ):
        """Initialize NORAA for a target ufsatm checkout."""
        repo_root = target_repo(repo)
        existing = load_project(repo_root)
        if existing and not force:
            raise SystemExit("Project already initialized. Use --force to overwrite.")

        origin = ""
        try:
            origin = get_origin_url(repo_root)
        except Exception:
            pass

        core_norm = normalize_core(core)
        default_verify_script = (
            "scripts/verify_fv3_smoke.sh" if core_norm == "fv3" else "scripts/verify_mpas_smoke.sh"
        )
        cfg = ProjectConfig(
            repo_path=str(repo_root),
            upstream_url=upstream_url,
            core=core_norm,
            verify_script=default_verify_script,
        )
        if origin and origin.rstrip("/") != upstream_url.rstrip("/"):
            if not typer.confirm("Repo is a fork. Proceed anyway?", default=False):
                raise SystemExit(2)
            cfg.allow_fork = True
            cfg.fork_url = origin

        write_project(repo_root, cfg)
        print("Initialized NORAA project.")

    @app.command()
    def doctor(repo: str = typer.Option(".", "--repo")):
        """Capture environment and tool snapshots for the target repo."""
        repo_root = target_repo(repo)
        require_project(repo_root)

        out = log_dir(repo_root, "doctor")
        env = os.environ.copy()
        write_env_snapshot(out, env)
        write_tool_snapshot(out, env)

        print((out / "tools.txt").read_text(), end="")
        print(f"Logs: {out}")

    @app.command()
    def verify(
        repo: str = typer.Option(".", "--repo"),
        core: str | None = typer.Option(
            None,
            "--core",
            help="Core to verify (mpas or fv3). Defaults to project config.",
        ),
        deps_prefix: str = typer.Option(None, "--deps-prefix"),
        esmf_mkfile: str = typer.Option(None, "--esmf-mkfile"),
        clean: bool = typer.Option(True, "--clean/--no-clean"),
        preflight_only: bool = typer.Option(False, "--preflight-only"),
        fv3_fms_r8_fallback: bool = typer.Option(
            True,
            "--fv3-fms-r8-fallback/--no-fv3-fms-r8-fallback",
            help="For FV3, auto-switch local ufsatm FMS component requirement R4->R8 when deps lack fms_r4.",
        ),
    ):
        """
        Verify that selected core can be configured and built for the target ufsatm repo.

        Behaviour:
        - Require ESMF (bootstrapped under .noraa/esmf, --esmf-mfile, or --deps-prefix).
        - Prefer upstream verify scripts when present; otherwise fall back to CMake.
        - Build the selected core only (MPAS or FV3).
        """
        repo_root = target_repo(repo)
        _run_verify(
            repo_root=repo_root,
            core=core,
            deps_prefix=deps_prefix,
            esmf_mkfile=esmf_mkfile,
            clean=clean,
            preflight_only=preflight_only,
            fv3_fms_r8_fallback=fv3_fms_r8_fallback,
        )

    @app.command()
    def bootstrap(
        repo: str = typer.Option(".", "--repo"),
        component: str = typer.Argument(...),
        esmf_branch: str = typer.Option(
            "v8.6.1",
            "--esmf-branch",
            help="ESMF git branch or tag to use when bootstrapping.",
        ),
    ):
        """
        Bootstrap required components under .noraa/ in the target repo.

        Supported components:
          - esmf -> clone, build, and install ESMF into .noraa/esmf/install
          - deps -> build bacio/bufr/sp/w3emc/pio into .noraa/deps/install
        """
        repo_root = target_repo(repo)
        require_project(repo_root)

        if component == "esmf":
            bootstrap_esmf(repo_root, esmf_branch)
            return
        if component == "deps":
            bootstrap_deps(repo_root)
            return

        fail(
            f"Unsupported bootstrap component: {component}",
            next_step=(
                f"{repo_cmd(repo_root, 'bootstrap', 'deps')}  or  "
                f"{repo_cmd(repo_root, 'bootstrap', 'esmf')}"
            ),
        )

    @app.command("build")
    def build(
        repo: str = typer.Option(".", "--repo"),
        core: str | None = typer.Option(
            None,
            "--core",
            help="Core to build (mpas or fv3). Defaults to project config.",
        ),
        clean: bool = typer.Option(True, "--clean/--no-clean"),
        yes: bool = typer.Option(
            False, "--yes", help="Auto-accept guided prompts and run all steps."
        ),
        esmf_branch: str = typer.Option(
            "v8.6.1",
            "--esmf-branch",
            help="ESMF git branch or tag to use if ESMF bootstrap is required.",
        ),
    ):
        """
        Guided one-command build path for a target ufsatm checkout.
        """
        repo_root = target_repo(repo)
        cfg = load_project(repo_root)
        selected_core = normalize_core(core or (cfg.core if cfg else "mpas"))
        guided_build.run_build_core(
            repo_root=repo_root,
            clean=clean,
            yes=yes,
            esmf_branch=esmf_branch,
            core=selected_core,
            confirm_fn=lambda msg: typer.confirm(msg, default=True),
            init_project_fn=lambda p: init(
                repo=str(p),
                force=False,
                core=selected_core,
                upstream_url="https://github.com/NOAA-EMC/ufsatm.git",
            ),
            require_project_fn=require_project,
            verify_fn=lambda p, do_clean, c: _run_verify(
                repo_root=p,
                core=c,
                deps_prefix=None,
                esmf_mkfile=None,
                clean=do_clean,
                preflight_only=False,
                fv3_fms_r8_fallback=True,
            ),
        )

    @app.command("build-mpas")
    def build_mpas(
        repo: str = typer.Option(".", "--repo"),
        clean: bool = typer.Option(True, "--clean/--no-clean"),
        yes: bool = typer.Option(
            False, "--yes", help="Auto-accept guided prompts and run all steps."
        ),
        esmf_branch: str = typer.Option(
            "v8.6.1",
            "--esmf-branch",
            help="ESMF git branch or tag to use if ESMF bootstrap is required.",
        ),
    ):
        """Backward-compatible alias for `noraa build --core mpas`."""
        build(repo=repo, core="mpas", clean=clean, yes=yes, esmf_branch=esmf_branch)

    verify_runner = lambda repo_root, core: _run_verify(
        repo_root=repo_root,
        core=core,
        deps_prefix=None,
        esmf_mkfile=None,
        clean=True,
        preflight_only=False,
        fv3_fms_r8_fallback=True,
    )
    compat = {
        "init": init,
        "doctor": doctor,
        "verify": verify,
        "bootstrap": bootstrap,
        "build": build,
        "build_mpas": build_mpas,
        "run_verify": _run_verify,
        "verify_preflight_issues": _verify_preflight_issues,
        "format_preflight_summary": _format_preflight_summary,
    }
    return verify_runner, compat
