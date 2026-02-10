from __future__ import annotations

from pathlib import Path
from typing import NoReturn


def repo_cmd(repo_root: Path, *parts: str) -> str:
    tokens = ["noraa", *parts, "--repo", str(repo_root)]
    return " ".join(tokens)


def fail(message: str, *, next_step: str | None = None, logs: Path | None = None) -> NoReturn:
    lines = [message]
    if logs is not None:
        lines.append(f"Logs: {logs}")
    if next_step:
        lines.append(f"Next step: {next_step}")
    raise SystemExit("\n".join(lines))

