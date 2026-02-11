from __future__ import annotations

from typing import Iterable

try:
    from rich.console import Console
    from rich.panel import Panel
except Exception:  # pragma: no cover
    Console = None
    Panel = None


def _print_panel(title: str, body: str) -> None:
    if Console is None or Panel is None:
        print(f"[{title}]")
        print(body)
        return
    Console().print(Panel(body, title=title, expand=False))


def notice(title: str, lines: Iterable[str]) -> None:
    body = "\n".join(lines)
    _print_panel(title, body)


def summary(*, fixes: list[str], next_step: str) -> None:
    lines: list[str] = []
    if fixes:
        lines.append("Fixes implemented (crashes avoided):")
        for i, fix in enumerate(fixes, start=1):
            lines.append(f"{i}. {fix}")
    else:
        lines.append("No automatic fixes were required in this run.")
    lines.append("")
    lines.append(f"Run this command next: {next_step}")
    notice("NORAA Summary", lines)
