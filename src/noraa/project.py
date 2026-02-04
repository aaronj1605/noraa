from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

try:
    import tomllib  # py3.11+
except Exception:  # pragma: no cover
    tomllib = None


@dataclass
class ProjectConfig:
    repo_path: str
    upstream_url: str = "https://github.com/NOAA-EMC/ufsatm.git"
    allow_fork: bool = False
    fork_url: str = ""
    verify_script: str = "scripts/verify_mpas_smoke.sh"


def _run_git(repo_root: Path, args: list[str]) -> str:
    out = subprocess.check_output(["git", "-C", str(repo_root), *args], text=True).strip()
    return out


def get_origin_url(repo_root: Path) -> str:
    return _run_git(repo_root, ["remote", "get-url", "origin"])


def project_file(repo_root: Path) -> Path:
    return repo_root / ".noraa" / "project.toml"


def load_project(repo_root: Path) -> ProjectConfig | None:
    p = project_file(repo_root)
    if not p.exists():
        return None
    raw = p.read_bytes()
    if tomllib is None:
        raise RuntimeError("tomllib not available. Use Python 3.11+.")
    data = tomllib.loads(raw.decode("utf-8"))
    project = data.get("project", {})
    git = data.get("git", {})
    build = data.get("build", {})

    return ProjectConfig(
        repo_path=str(project.get("repo_path", str(repo_root))),
        upstream_url=str(git.get("upstream_url", "https://github.com/NOAA-EMC/ufsatm.git")),
        allow_fork=bool(git.get("allow_fork", False)),
        fork_url=str(git.get("fork_url", "")),
        verify_script=str(build.get("verify_script", "scripts/verify_mpas_smoke.sh")),
    )


def write_project(repo_root: Path, cfg: ProjectConfig) -> Path:
    p = project_file(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)

    text = (
        "[project]\n"
        f'repo_path = "{cfg.repo_path}"\n'
        "\n"
        "[git]\n"
        f'upstream_url = "{cfg.upstream_url}"\n'
        f"allow_fork = {'true' if cfg.allow_fork else 'false'}\n"
        f'fork_url = "{cfg.fork_url}"\n'
        "\n"
        "[build]\n"
        f'verify_script = "{cfg.verify_script}"\n'
    )
    p.write_text(text)
    return p


def validate_repo_origin(repo_root: Path, cfg: ProjectConfig) -> tuple[bool, str]:
    origin = get_origin_url(repo_root)

    def norm(u: str) -> str:
        return u.rstrip("/")

    upstream_ok = norm(origin) == norm(cfg.upstream_url)
    if upstream_ok:
        return True, f"origin matches upstream: {origin}"

    if cfg.allow_fork:
        if cfg.fork_url and norm(origin) == norm(cfg.fork_url):
            return True, f"origin matches configured fork: {origin}"
        return False, f"origin does not match configured fork. origin={origin} fork_url={cfg.fork_url}"

    return False, f"origin is not upstream. origin={origin} upstream={cfg.upstream_url}"
