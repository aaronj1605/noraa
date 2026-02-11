from __future__ import annotations

from pathlib import Path
from typing import NoReturn


def _fmt_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def repo_cmd(repo_root: Path, *parts: str) -> str:
    tokens = ["noraa", *parts, "--repo", _fmt_path(repo_root)]
    return " ".join(tokens)


def fail(message: str, *, next_step: str | None = None, logs: Path | None = None) -> NoReturn:
    lines = [message]
    if logs is not None:
        lines.append(f"Logs: {_fmt_path(logs)}")
    if next_step:
        lines.append(f"Run this command next: {next_step}")
    raise SystemExit("\n".join(lines))
