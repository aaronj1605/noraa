from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import textwrap
import yaml
import importlib.resources as resources


@dataclass
class Rule:
    id: str
    title: str
    severity: str
    match_any_regex: list[str]
    summary: str
    likely_cause: str
    evidence_commands: list[str]
    fix_steps: list[str]


def _load_rules() -> list[Rule]:
    rules: list[Rule] = []
    pkg = resources.files("noraa.agent.rules")
    for p in pkg.iterdir():
        if p.name.endswith(".yml") or p.name.endswith(".yaml"):
            data = yaml.safe_load(p.read_text())
            rules.append(
                Rule(
                    id=data["id"],
                    title=data.get("title", ""),
                    severity=data.get("severity", "error"),
                    match_any_regex=list(data.get("match_any_regex", [])),
                    summary=str(data.get("summary", "")).strip(),
                    likely_cause=str(data.get("likely_cause", "")).strip(),
                    evidence_commands=list(data.get("evidence_commands", [])),
                    fix_steps=list(data.get("fix_steps", [])),
                )
            )
    return rules


def _read_text(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except Exception:
        return ""


def diagnose_log(
    log_dir: Path,
    repo_root: Path,
    deps_prefix: str | None = None,
) -> tuple[int, str]:
    stdout = _read_text(log_dir / "stdout.txt")
    stderr = _read_text(log_dir / "stderr.txt")
    combined = stdout + "\n" + stderr

    rules = _load_rules()
    hits: list[Rule] = []
    for r in rules:
        for pat in r.match_any_regex:
            if re.search(pat, combined, flags=re.IGNORECASE | re.MULTILINE):
                hits.append(r)
                break

    if not hits:
        msg = "No known rule matched this failure. Review logs and snapshot files in: " + str(log_dir)
        return 2, msg + "\n"

    r = hits[0]
    mpas_exe = repo_root / ".noraa" / "build" / "bin" / "mpas_atmosphere"
    substitutions = {
        "deps_prefix": deps_prefix or "<set DEPS_PREFIX>",
        "repo_root": str(repo_root),
        "mpas_exe": str(mpas_exe),
    }

    def fmt(line: str) -> str:
        return line.format(**substitutions)

    fix = "\n".join(fmt(s) for s in r.fix_steps)
    evidence = "\n".join(fmt(s) for s in r.evidence_commands)

    out = textwrap.dedent(
        f"""\
        Diagnosis: {r.title}
        Rule: {r.id}
        Summary: {r.summary}

        Likely cause:
        {r.likely_cause}

        Evidence commands:
        {evidence}

        Fix steps (copy/paste):
        {fix}
        """
    ).rstrip() + "\n"

    return 2, out
