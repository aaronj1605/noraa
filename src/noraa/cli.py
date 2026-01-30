from pathlib import Path
from datetime import datetime
import shutil
import subprocess
import typer

app = typer.Typer(add_completion=False)

def git_root(path: Path) -> Path:
    out = subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        text=True,
    ).strip()
    return Path(out)

def log_dir(repo_root: Path, action: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = repo_root / ".noraa" / "logs" / f"{ts}-{action}"
    d.mkdir(parents=True, exist_ok=True)
    return d

def run_cmd(cmd, cwd, out_dir):
    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out, err = p.communicate()
    (out_dir / "command.txt").write_text(" ".join(cmd))
    (out_dir / "stdout.txt").write_text(out or "")
    (out_dir / "stderr.txt").write_text(err or "")
    return p.returncode

def tool(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None

@app.command()
def doctor(path: str = "."):
    repo = git_root(Path(path).resolve())
    out = log_dir(repo, "doctor")
    tools = {
        "git": tool("git"),
        "python": tool("python3", "python"),
        "cmake": tool("cmake"),
        "make": tool("make", "gmake"),
        "mpiexec": tool("mpiexec", "mpirun"),
    }
    ok = True
    lines = []
    for k, v in tools.items():
        if v:
            lines.append(f"[ok] {k}: {v}")
        else:
            ok = False
            lines.append(f"[missing] {k}")
    (out / "doctor.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"Logs: {out}")
    raise SystemExit(0 if ok else 2)

@app.command()
def init(path: str = ".", force: bool = False):
    repo = git_root(Path(path).resolve())
    cfg = repo / ".noraa" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if cfg.exists() and not force:
        print(f"Config already exists: {cfg}")
        print("Use --force to overwrite.")
        return
    cfg.write_text('version = 1\n[verify]\nscript = "scripts/verify_mpas_smoke.sh"\n')
    print(f"Wrote {cfg}")

@app.command()
def verify(path: str = "."):
    repo = git_root(Path(path).resolve())
    out = log_dir(repo, "verify")
    script = repo / "scripts" / "verify_mpas_smoke.sh"
    if not script.exists():
        raise SystemExit(f"Missing verify script: {script}")
    rc = run_cmd(["bash", str(script)], str(repo), out)
    (out / "result.txt").write_text(f"exit_code={rc}\n")
    if rc != 0:
        print(f"VERIFY FAILED (exit {rc}). Logs: {out}")
        raise SystemExit(rc)
    print(f"VERIFY PASSED. Logs: {out}")

if __name__ == "__main__":
    app()
