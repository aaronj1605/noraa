from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil
import subprocess
import sys
import selectors


def git_root(path: Path) -> Path:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
        return Path(out)
    except Exception as exc:
        raise RuntimeError(f"not a git repository: {path}") from exc


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
    """
    Run a command while:
      - writing stdout to stdout.txt and stderr to stderr.txt
      - also printing to the terminal so it does not look frozen
    """
    cmd_path = out_dir / "command.txt"
    with cmd_path.open("a") as cf:
        cf.write(" ".join(cmd) + "\n")

    stdout_path = out_dir / "stdout.txt"
    stderr_path = out_dir / "stderr.txt"

    with stdout_path.open("w") as so, stderr_path.open("w") as se:
        p = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1,
        )

        assert p.stdout is not None
        assert p.stderr is not None

        sel = selectors.DefaultSelector()
        sel.register(p.stdout, selectors.EVENT_READ, data=("stdout", so))
        sel.register(p.stderr, selectors.EVENT_READ, data=("stderr", se))

        # Read until both pipes close
        while sel.get_map():
            for key, _ in sel.select(timeout=0.2):
                label, f = key.data
                line = key.fileobj.readline()
                if line == "":
                    try:
                        sel.unregister(key.fileobj)
                    except Exception:
                        pass
                    continue

                f.write(line)
                f.flush()

                if label == "stdout":
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    sys.stderr.write(line)
                    sys.stderr.flush()

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
