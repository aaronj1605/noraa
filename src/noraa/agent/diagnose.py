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
    esmf_mkfile: str | None = None,
) -> tuple[int, str, str | None, str | None]:
    stdout = _read_text(log_dir / "stdout.txt")
    stderr = _read_text(log_dir / "stderr.txt")
    combined = stdout + "\n" + stderr

    rules = _load_rules()
    hit: Rule | None = None
    for r in rules:
        for pat in r.match_any_regex:
            if re.search(pat, combined, flags=re.IGNORECASE | re.MULTILINE):
                hit = r
                break
        if hit:
            break

    if not hit:
        msg = "No known rule matched this failure. Review logs and snapshot files in: " + str(log_dir)
        return 2, msg + "\n", None, None

    mpas_exe = repo_root / ".noraa" / "build" / "bin" / "mpas_atmosphere"

    substitutions = {
        "deps_prefix": deps_prefix or "<set DEPS_PREFIX>",
        "repo_root": str(repo_root),
        "mpas_exe": str(mpas_exe),
        "mpi_prefix": "",
        "mpiexec_real": "",
        "esmf_src": "<path to ESMF source>",
        "esmf_install_prefix": "<path to ESMF install prefix>",
        "esmf_mkfile": esmf_mkfile or "<path to esmf.mk>",
    }

    # Optional: enrich substitutions from postcheck.txt if present
    postcheck = log_dir / "postcheck.txt"
    if postcheck.exists():
        for ln in postcheck.read_text().splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                substitutions[k.strip()] = v.strip()

    def fmt(line: str) -> str:
        return line.format(**substitutions)

    evidence = "\n".join(fmt(s) for s in hit.evidence_commands)
    fix = "\n".join(fmt(s) for s in hit.fix_steps)

    msg = textwrap.dedent(
        f"""\
        Diagnosis: {hit.title}
        Rule: {hit.id}
        Summary: {hit.summary}

        Likely cause:
        {hit.likely_cause}

        Evidence commands:
        {evidence}

        Fix steps (copy/paste):
        {fix}
        """
    ).rstrip() + "\n"

    script = "#!/usr/bin/env bash\nset -euo pipefail\n\n" + fix.strip() + "\n"
    return 2, msg, hit.id, script
