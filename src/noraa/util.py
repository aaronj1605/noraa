from __future__ import annotations

from pathlib import Path
import subprocess
import shutil
from datetime import datetime


def git_root(path: Path) -> Path:
    out = subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        text=True,
    ).strip()
    return Path(out)


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir(repo_root: Path, action: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = repo_root / ".noraa" / "logs" / f"{ts}-{action}"
    ensure_dir(d)
    return d


def which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def run_streamed(cmd: list[str], cwd: Path, out_dir: Path, env: dict[str, str]) -> int:
    (out_dir / "command.txt").write_text(" ".join(cmd) + "\n")
    stdout_path = out_dir / "stdout.txt"
    stderr_path = out_dir / "stderr.txt"
    with stdout_path.open("w") as so, stderr_path.open("w") as se:
        p = subprocess.Popen(cmd, cwd=str(cwd), stdout=so, stderr=se, text=True, env=env)
        return p.wait()


def safe_check_output(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    try:
        return subprocess.check_output(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            stderr=subprocess.STDOUT,
        )
    except Exception as e:
        return f"[command failed] {' '.join(cmd)}\n{e}\n"
