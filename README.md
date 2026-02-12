# NORAA

NORAA is a CLI helper for building UFS ATM with MPAS (not FV3) using clean upstream repos.
It keeps build artifacts and logs under `.noraa/` in the target `ufsatm` repo for reproducible runs.

## Tested Baseline

- Linux: Ubuntu 22.04 / 24.04
- Python: 3.11+
- UFS repo: `NOAA-EMC/ufsatm` branch `develop`

## Prerequisites (Clean Ubuntu)

Run:

```bash
sudo apt update
sudo apt install -y \
  git curl ca-certificates build-essential cmake ninja-build pkg-config \
  gfortran m4 perl flex bison patch rsync file \
  python3 python3-venv python3-pip \
  openmpi-bin libopenmpi-dev \
  libnetcdf-dev libnetcdff-dev libpnetcdf-dev pnetcdf-bin
```

What these are for:

- `git curl ca-certificates`: clone/fetch source repositories securely.
- `build-essential cmake ninja-build pkg-config`: core C/C++/Fortran build tooling.
- `gfortran m4 perl flex bison patch rsync file`: Fortran preprocessing/generation and utility tools used in dependency and model builds.
- `python3 python3-venv python3-pip`: required Python runtime and isolated environment for NORAA.
- `openmpi-bin libopenmpi-dev`: MPI runtime/compiler wrappers used by MPAS/UFS builds.
- `libnetcdf-dev libnetcdff-dev libpnetcdf-dev pnetcdf-bin`: NetCDF/PnetCDF headers/libs and `pnetcdf-config` used by dependency bootstrap.
- `awscli` (optional): required only when fetching UFS/HTF datasets from `noaa-ufs-htf-pds` with `noraa run-smoke fetch-data official-ufs`.

If your image provides `python3.11`, you can use it explicitly. Otherwise use `python3`.

## Why Use a venv

Use a virtual environment so NORAA dependencies are isolated from system Python.
This keeps installs reproducible and avoids breaking OS-level Python tooling.

## Clean Environment Flow

```bash
mkdir -p ~/work && cd ~/work

# NORAA repo + install

git clone https://github.com/aaronj1605/noraa.git
cd noraa
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .

# Clean upstream ufsatm target
cd ~/work
git clone --branch develop https://github.com/NOAA-EMC/ufsatm.git
cd ufsatm

# NORAA guided workflow (recommended)
noraa build-mpas --repo ~/work/ufsatm
```

## Manual Workflow (Advanced)

Use this if you want each stage explicitly:

```bash
noraa init --repo ~/work/ufsatm
noraa verify --preflight-only --repo ~/work/ufsatm
noraa bootstrap esmf --repo ~/work/ufsatm
noraa bootstrap deps --repo ~/work/ufsatm
noraa verify --repo ~/work/ufsatm
```

## Command Purpose

- noraa init: create .noraa/project.toml for this ufsatm checkout so NORAA can track repo-local state, logs, and artifacts. Run once per clone.
- noraa build-mpas: guided one-command path for fresh setups. It prompts through init, submodule update, dependency bootstrap, and MPAS verify.
- noraa verify --preflight-only: run fast blockers-only checks before a full build. It reports what is missing and the exact next command to run.
- noraa bootstrap esmf: build ESMF into .noraa/esmf/install so MPAS and UFS can find ESMF without requiring a system-wide install.
- noraa bootstrap deps: build MPAS and UFS support libraries into .noraa/deps/install so CMake can resolve required packages during verify.
- noraa verify: run the MPAS-only configure and build validation (MPAS=ON, FV3=OFF) and write detailed logs under .noraa/logs/.... Treat this as the main pass/fail build check.
- noraa run-smoke status: show RED/GREEN readiness checks for optional smoke execution and print required follow-up actions.
- noraa run-smoke status --short: print one-line readiness summary for quick checks/automation.
- noraa run-smoke validate-data: validate dataset runtime compatibility and print first blocking issue/action.
- noraa run-smoke fetch-data scan|official|local: register smoke-run dataset metadata from scanned repo files, curated official test-case URLs, or user-local files.
- noraa run-smoke fetch-data official-ufs: fetch case data from `noaa-ufs-htf-pds` (AWS Open Data) and record required citation metadata.
- noraa run-smoke fetch-data official-regtests: fetch candidate case data from `noaa-ufs-regtests-pds` and validate runtime compatibility via status checks.
- noraa run-smoke fetch-data local --dry-run: validate local case directory without importing.
- noraa run-smoke fetch-data official-regtests --dry-run: preview source path without downloading.
- noraa run-smoke fetch-data clean-data: remove one dataset or all smoke data under `.noraa/runs/smoke/data`.
- noraa run-smoke execute: run a short, structured smoke execution probe after readiness is GREEN and write command/stdout/stderr/result under .noraa/runs/smoke/exec/.

## Logs and Artifacts

NORAA writes outputs under target repo `.noraa/`:

- `.noraa/logs/<timestamp>-*` command logs
- `.noraa/esmf/` bootstrapped ESMF
- `.noraa/deps/` bootstrapped dependencies
- `.noraa/build/` verify build directory


## License and Attribution

Attribution request: If you use this project in research, teaching, demos, derivative tools, or redistributed builds, please provide visible credit to NORAA and link this repository.
