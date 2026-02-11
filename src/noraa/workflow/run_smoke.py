from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..buildsystem.paths import bootstrapped_deps_prefix, bootstrapped_esmf_mk
from ..messages import repo_cmd


@dataclass(frozen=True)
class StatusCheck:
    name: str
    ok: bool
    detail: str
    action_required: str | None = None


def collect_status_checks(repo_root: Path) -> list[StatusCheck]:
    project_file = repo_root / ".noraa" / "project.toml"
    ccpp_prebuild = repo_root / "ccpp" / "framework" / "scripts" / "ccpp_prebuild.py"
    deps_prefix = bootstrapped_deps_prefix(repo_root)
    esmf_mk = bootstrapped_esmf_mk(repo_root)
    mpas_exe = repo_root / ".noraa" / "build" / "bin" / "mpas_atmosphere"
    smoke_data = repo_root / ".noraa" / "runs" / "smoke" / "data"
    smoke_has_files = smoke_data.exists() and any(smoke_data.iterdir())

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
            ok=smoke_has_files,
            detail=str(smoke_data),
            action_required=f"Place sample input files under {smoke_data}",
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
