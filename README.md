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
  python3.11 python3.11-venv python3-pip \
  openmpi-bin libopenmpi-dev \
  libnetcdf-dev libnetcdff-dev libpnetcdf-dev pnetcdf-bin
```

What these are for:

- `git curl ca-certificates`: clone/fetch source repositories securely.
- `build-essential cmake ninja-build pkg-config`: core C/C++/Fortran build tooling.
- `gfortran m4 perl flex bison patch rsync file`: Fortran preprocessing/generation and utility tools used in dependency and model builds.
- `python3.11 python3.11-venv python3-pip`: required Python runtime and isolated environment for NORAA.
- `openmpi-bin libopenmpi-dev`: MPI runtime/compiler wrappers used by MPAS/UFS builds.
- `libnetcdf-dev libnetcdff-dev libpnetcdf-dev pnetcdf-bin`: NetCDF/PnetCDF headers/libs and `pnetcdf-config` used by dependency bootstrap.

## Why Use a venv

Use a virtual environment so NORAA dependencies are isolated from system Python.
This keeps installs reproducible and avoids breaking OS-level Python tooling.

## Clean Environment Flow

```bash
mkdir -p ~/work && cd ~/work

# NORAA repo + install

git clone https://github.com/aaronj1605/noraa.git
cd noraa
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .

# Clean upstream ufsatm target
cd ~/work
git clone --branch develop https://github.com/NOAA-EMC/ufsatm.git
cd ufsatm

# NORAA workflow
noraa init
noraa verify --preflight-only
noraa bootstrap esmf
noraa bootstrap deps
noraa verify
```

## Command Purpose

- `noraa init`: creates `.noraa/project.toml` in target repo.
- `noraa verify --preflight-only`: reports blocking issues and actions before build.
- `noraa bootstrap esmf`: builds ESMF into `.noraa/esmf/install`.
- `noraa bootstrap deps`: builds MPAS/UFS deps into `.noraa/deps/install`.
- `noraa verify`: runs MPAS-only verify build (`MPAS=ON`, `FV3=OFF`).

## Logs and Artifacts

NORAA writes outputs under target repo `.noraa/`:

- `.noraa/logs/<timestamp>-*` command logs
- `.noraa/esmf/` bootstrapped ESMF
- `.noraa/deps/` bootstrapped dependencies
- `.noraa/build/` verify build directory


## License and Attribution

This project is released under the MIT License. See LICENSE.

Attribution request:
If you use this project in research, teaching, demos, derivative tools, or redistributed builds, please provide visible credit to Aaron Jones and link back to this repository.
