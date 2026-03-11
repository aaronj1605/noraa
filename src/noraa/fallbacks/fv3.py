from __future__ import annotations

import re
from pathlib import Path


def _apply_fv3_fms_r8_fallback(repo_root: Path, deps_prefix: str | None = None) -> bool:
    """
    For FV3, patch local ufsatm CMake line from R4 to R8 when present so
    configure can proceed on toolchains/deps that do not expose FMS::fms_r4.
    """
    cmake_file = repo_root / "fv3" / "atmos_cubed_sphere" / "CMakeLists.txt"
    if not cmake_file.exists():
        return False
    text = cmake_file.read_text()
    # Rewrite any find_package(FMS ...) block to a deterministic R8 request.
    # This handles single-line, multiline, and variable-driven forms.
    block_pattern = re.compile(r"find_package\((?P<body>[\s\S]*?)\)", re.IGNORECASE)

    def _rewrite(match: re.Match[str]) -> str:
        block = match.group(0)
        body = match.group("body")
        if "fms" not in body.lower():
            return block
        normalized = re.sub(r"\s+", " ", block.strip()).lower()
        target = "find_package(fms components r8 required)"
        if normalized == target:
            return block
        return "find_package(FMS COMPONENTS R8 REQUIRED)"

    patched = block_pattern.sub(_rewrite, text)
    if patched == text:
        return False
    cmake_file.write_text(patched)
    return True


def _apply_fv3_fms_required_fallback(repo_root: Path) -> bool:
    """
    Final FV3 fallback: remove FMS component constraints and require plain FMS.
    """
    cmake_file = repo_root / "fv3" / "atmos_cubed_sphere" / "CMakeLists.txt"
    if not cmake_file.exists():
        return False
    text = cmake_file.read_text()
    block_pattern = re.compile(r"find_package\((?P<body>[\s\S]*?)\)", re.IGNORECASE)

    def _rewrite(match: re.Match[str]) -> str:
        block = match.group(0)
        body = match.group("body")
        if "fms" not in body.lower():
            return block
        normalized = re.sub(r"\s+", " ", block.strip()).lower()
        target = "find_package(fms required)"
        if normalized == target:
            return block
        return "find_package(FMS REQUIRED)"

    patched = block_pattern.sub(_rewrite, text)
    canonical_fms_block = (
        "if(NOT FMS_FOUND)\n"
        "  find_package(FMS REQUIRED)\n"
        "  string(TOLOWER ${kind} kind_lower)\n"
        "  if(TARGET FMS::fms)\n"
        "    add_library(fms ALIAS FMS::fms)\n"
        "  endif()\n"
        "endif()\n"
    )
    start = patched.find("if(NOT FMS_FOUND)")
    marker = "list(APPEND model_srcs"
    if start != -1:
        end = patched.find(marker, start)
        if end != -1:
            patched = patched[:start] + canonical_fms_block + patched[end:]
    patched = re.sub(
        r"(?:if\(TARGET\s+FMS::fms\)\s*){2,}",
        "if(TARGET FMS::fms)\n",
        patched,
        flags=re.IGNORECASE,
    )
    # Normalize hard-coded component targets to the default FMS target.
    patched = re.sub(r"FMS::fms_r[48]\b", "FMS::fms", patched, flags=re.IGNORECASE)
    # Guard alias creation so configure does not fail when upstream FMS package
    # does not expose component targets.
    alias_guard = "if(TARGET FMS::fms)\n  add_library(fms ALIAS FMS::fms)\nendif()"
    patched = re.sub(
        r"add_library\(\s*fms\s+ALIAS\s+FMS::\$\{[^\}]+\}\s*\)",
        "add_library(fms ALIAS FMS::fms)",
        patched,
        flags=re.IGNORECASE,
    )
    patched = re.sub(
        r"add_library\(\s*fms\s+ALIAS\s+FMS::fms\s*\)",
        alias_guard,
        patched,
        flags=re.IGNORECASE,
    )
    patched = re.sub(
        r"if\(TARGET\s+FMS::fms\)\s*if\(TARGET\s+FMS::fms\)\s*add_library\(\s*fms\s+ALIAS\s+FMS::fms\s*\)\s*endif\(\)\s*endif\(\)",
        alias_guard,
        patched,
        flags=re.IGNORECASE,
    )
    # Final direct safeguards for common exact forms.
    patched = patched.replace(
        "find_package(FMS COMPONENTS R4 REQUIRED)",
        "find_package(FMS REQUIRED)",
    )
    patched = patched.replace(
        "find_package(FMS COMPONENTS R8 REQUIRED)",
        "find_package(FMS REQUIRED)",
    )
    patched = patched.replace(
        "add_library(fms ALIAS FMS::fms_r4)",
        "if(TARGET FMS::fms)\n  add_library(fms ALIAS FMS::fms)\nendif()",
    )
    patched = patched.replace(
        "add_library(fms ALIAS FMS::fms_r8)",
        "if(TARGET FMS::fms)\n  add_library(fms ALIAS FMS::fms)\nendif()",
    )
    if patched == text:
        return False
    cmake_file.write_text(patched)
    return True


def _apply_fv3_top_level_dependency_guards(repo_root: Path) -> bool:
    """
    Guard known optional FV3 dependency edges in ufsatm top-level CMake so
    configure does not fail when optional targets are absent.
    """
    cmake_file = repo_root / "CMakeLists.txt"
    if not cmake_file.exists():
        return False
    text = cmake_file.read_text()

    dep_pattern = re.compile(
        r"add_dependencies\((?P<body>[\s\S]*?)\)",
        flags=re.IGNORECASE,
    )
    changed = False

    def _rewrite(match: re.Match[str]) -> str:
        nonlocal changed
        body = match.group("body")
        tokens = body.split()
        if len(tokens) < 2 or "stochastic_physics" not in tokens:
            return match.group(0)
        changed = True
        target = tokens[0]
        deps = tokens[1:]
        filtered_deps = [t for t in deps if t != "stochastic_physics"]
        if filtered_deps:
            return f"add_dependencies({target} {' '.join(filtered_deps)})"
        return ""

    patched = dep_pattern.sub(_rewrite, text)

    # Also strip stochastic_physics from direct link libraries on ufsatm_fv3,
    # since non-exported targets break install(EXPORT) generation.
    link_pattern = re.compile(
        r"target_link_libraries\((?P<body>[\s\S]*?)\)",
        flags=re.IGNORECASE,
    )

    def _rewrite_link(match: re.Match[str]) -> str:
        nonlocal changed
        body = match.group("body")
        tokens = body.split()
        if len(tokens) < 2 or "stochastic_physics" not in tokens:
            return match.group(0)
        changed = True
        target = tokens[0]
        rest = tokens[1:]
        filtered = [t for t in rest if t != "stochastic_physics"]
        return f"target_link_libraries({target} {' '.join(filtered)})"

    patched = link_pattern.sub(_rewrite_link, patched)
    # Some previous fallback passes can leave an empty
    # if(TARGET stochastic_physics) ... endif() block; remove it.
    patched = re.sub(
        r"if\(TARGET\s+stochastic_physics\)\s*[\s\S]*?endif\(\)\s*",
        "",
        patched,
        flags=re.IGNORECASE,
    )
    if patched == text:
        return False
    cmake_file.write_text(patched)
    return True


def _apply_fv3_external_sst_fallback(repo_root: Path) -> bool:
    """
    Patch FV3 external_sst module for newer FMS amip_interp_mod APIs that no
    longer export sst_ncep/sst_anom symbols.
    """
    f90 = repo_root / "fv3" / "atmos_cubed_sphere" / "tools" / "external_sst.F90"
    if not f90.exists():
        return False
    text = f90.read_text()
    patched = re.sub(
        r"use amip_interp_mod,\s*only:\s*i_sst,\s*j_sst,\s*sst_ncep,\s*sst_anom,\s*&\s*\n\s*forecast_mode,\s*use_ncep_sst",
        (
            "use amip_interp_mod, only: i_sst, j_sst, forecast_mode, use_ncep_sst\n"
            "real, allocatable, dimension(:,:) ::  sst_ncep, sst_anom"
        ),
        text,
        flags=re.IGNORECASE,
    )
    if patched == text:
        return False
    f90.write_text(patched)
    return True


def _apply_fv3_stochastic_wrapper_stub(repo_root: Path) -> bool:
    """
    Replace FV3 stochastic physics wrapper with a no-op compatibility stub when
    required module providers are unavailable in the current build graph.
    """
    f90 = (
        repo_root
        / "fv3"
        / "stochastic_physics"
        / "stochastic_physics_wrapper.F90"
    )
    if not f90.exists():
        return False
    text = f90.read_text()
    if "NORAA_FV3_STOCH_STUB" in text:
        return False
    stub = (
        "module stochastic_physics_wrapper_mod\n"
        "  ! NORAA_FV3_STOCH_STUB: local fallback for missing stochastic_physics.mod\n"
        "  implicit none\n"
        "  public stochastic_physics_wrapper, stochastic_physics_wrapper_end\n"
        "contains\n"
        "  subroutine stochastic_physics_wrapper (GFS_Control, GFS_Statein, GFS_Grid, GFS_Sfcprop, GFS_Radtend, GFS_Coupling, Atm_block, ierr)\n"
        "    use GFS_typedefs, only: GFS_control_type, GFS_statein_type, GFS_grid_type, GFS_sfcprop_type, GFS_radtend_type, GFS_coupling_type\n"
        "    use block_control_mod, only: block_control_type\n"
        "    implicit none\n"
        "    type(GFS_control_type),   intent(inout) :: GFS_Control\n"
        "    type(GFS_statein_type),   intent(in)    :: GFS_Statein\n"
        "    type(GFS_grid_type),      intent(in)    :: GFS_Grid\n"
        "    type(GFS_sfcprop_type),   intent(inout) :: GFS_Sfcprop\n"
        "    type(GFS_radtend_type),   intent(inout) :: GFS_Radtend\n"
        "    type(GFS_coupling_type),  intent(inout) :: GFS_Coupling\n"
        "    type(block_control_type), intent(inout) :: Atm_block\n"
        "    integer,                  intent(out)   :: ierr\n"
        "    ierr = 0\n"
        "  end subroutine stochastic_physics_wrapper\n"
        "\n"
        "  subroutine stochastic_physics_wrapper_end (GFS_Control)\n"
        "    use GFS_typedefs, only: GFS_control_type\n"
        "    implicit none\n"
        "    type(GFS_control_type), intent(inout) :: GFS_Control\n"
        "  end subroutine stochastic_physics_wrapper_end\n"
        "end module stochastic_physics_wrapper_mod\n"
    )
    f90.write_text(stub)
    return True


def _apply_fv3_update_ca_fallback(repo_root: Path) -> bool:
    """
    Disable optional update_ca restart hooks when update_ca.mod is unavailable.
    """
    f90 = repo_root / "fv3" / "atmos_model.F90"
    if not f90.exists():
        return False
    text = f90.read_text()
    patched = re.sub(
        r"^[ \t]*use update_ca,\s*only:\s*read_ca_restart\s*$",
        "  ! NORAA_FV3_UPDATE_CA_FALLBACK: update_ca unavailable",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    patched = re.sub(
        r"^[ \t]*use update_ca,\s*only:\s*write_ca_restart\s*$",
        "  ! NORAA_FV3_UPDATE_CA_FALLBACK: update_ca unavailable",
        patched,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    patched = re.sub(
        r"^[ \t]*use get_stochy_pattern_mod,\s*only:\s*write_stoch_restart_atm\s*$",
        "  ! NORAA_FV3_UPDATE_CA_FALLBACK: get_stochy_pattern_mod unavailable",
        patched,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    patched = re.sub(
        r"^[ \t]*call read_ca_restart\s*\(.*\)\s*$",
        "      ! NORAA_FV3_UPDATE_CA_FALLBACK: skipped read_ca_restart",
        patched,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    patched = re.sub(
        r"^[ \t]*call write_ca_restart\s*\(.*\)\s*$",
        "       ! NORAA_FV3_UPDATE_CA_FALLBACK: skipped write_ca_restart",
        patched,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if patched == text:
        return False
    f90.write_text(patched)
    return True


def _apply_fv3_stochy_pattern_fallback(repo_root: Path) -> bool:
    """
    Disable optional get_stochy_pattern_mod hooks when the module is absent.
    """
    fv3_dir = repo_root / "fv3"
    if not fv3_dir.exists():
        return False
    changed_any = False
    for rel in ("atmos_model.F90", "module_fcst_grid_comp.F90"):
        f90 = fv3_dir / rel
        if not f90.exists():
            continue
        text = f90.read_text()
        patched = re.sub(
            r"^[ \t]*use get_stochy_pattern_mod,\s*only:\s*write_stoch_restart_atm\s*$",
            "  ! NORAA_FV3_STOCHY_PATTERN_FALLBACK: get_stochy_pattern_mod unavailable",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        patched = re.sub(
            r"^[ \t]*call write_stoch_restart_atm\s*\(.*\)\s*$",
            "          ! NORAA_FV3_STOCHY_PATTERN_FALLBACK: skipped write_stoch_restart_atm",
            patched,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if patched != text:
            f90.write_text(patched)
            changed_any = True
    return changed_any


def _apply_fv3_fv_dynamics_kind_fix(repo_root: Path) -> bool:
    """
    Normalize fv_dynamics interstitial pointer declarations to plain REAL.
    This repairs local edits that force an incompatible kind on these fields.
    """
    f90 = repo_root / "fv3" / "atmos_cubed_sphere" / "model" / "fv_dynamics.F90"
    if not f90.exists():
        return False
    text = f90.read_text()
    patched = text
    pointer_specs = (
        ("cappa", ":,:,:"),
        ("dp1", ":,:,:"),
        ("dtdt_m", ":,:,:"),
        ("te_2d", ":,:"),
    )
    for name, dims in pointer_specs:
        patched = re.sub(
            rf"^[ \t]*real(?:\s*\(\s*[0-9]+\s*\)|\s*\(\s*kind\s*=\s*[A-Za-z_][A-Za-z0-9_]*\s*\))?\s*,\s*(?:dimension\(\s*{re.escape(dims)}\s*\)\s*,\s*pointer|pointer\s*,\s*dimension\(\s*{re.escape(dims)}\s*\))\s*::\s*{name}\s*$",
            f"      real, dimension({dims}), pointer :: {name}",
            patched,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    if patched == text:
        return False
    f90.write_text(patched)
    return True
