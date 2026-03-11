from __future__ import annotations

from pathlib import Path

from noraa import cli


def test_apply_fv3_fms_r8_fallback_patches_when_r4_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "fv3" / "atmos_cubed_sphere" / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text("find_package(FMS COMPONENTS R4 REQUIRED)\n")

    changed = cli._apply_fv3_fms_r8_fallback(repo, deps_prefix=None)
    assert changed is True
    assert "find_package(FMS COMPONENTS R8 REQUIRED)" in cmake.read_text()


def test_apply_fv3_fms_r8_fallback_patches_even_when_r4_exists(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "fv3" / "atmos_cubed_sphere" / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    original = "find_package(FMS COMPONENTS R4 REQUIRED)\n"
    cmake.write_text(original)

    changed = cli._apply_fv3_fms_r8_fallback(repo, deps_prefix=str(tmp_path / "deps"))
    assert changed is True
    assert "find_package(FMS COMPONENTS R8 REQUIRED)" in cmake.read_text()


def test_apply_fv3_fms_r8_fallback_patches_multiline_find_package(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "fv3" / "atmos_cubed_sphere" / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text(
        "find_package(\n"
        "  FMS\n"
        "  COMPONENTS R4\n"
        "  REQUIRED\n"
        ")\n"
    )
    changed = cli._apply_fv3_fms_r8_fallback(repo)
    assert changed is True
    updated = cmake.read_text()
    assert "COMPONENTS R8" in updated
    assert "COMPONENTS R4" not in updated


def test_apply_fv3_fms_r8_fallback_patches_variable_form(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "fv3" / "atmos_cubed_sphere" / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text(
        "set(FMS_COMPONENTS R4)\n"
        "find_package(FMS COMPONENTS ${FMS_COMPONENTS} REQUIRED)\n"
    )
    changed = cli._apply_fv3_fms_r8_fallback(repo)
    assert changed is True
    assert "find_package(FMS COMPONENTS R8 REQUIRED)" in cmake.read_text()


def test_apply_fv3_fms_required_fallback_rewrites_to_required(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "fv3" / "atmos_cubed_sphere" / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text("find_package(FMS COMPONENTS R8 REQUIRED)\n")
    changed = cli._apply_fv3_fms_required_fallback(repo)
    assert changed is True
    assert cmake.read_text().strip() == "find_package(FMS REQUIRED)"


def test_apply_fv3_fms_required_fallback_normalizes_target_refs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "fv3" / "atmos_cubed_sphere" / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text(
        "find_package(FMS COMPONENTS R8 REQUIRED)\n"
        "add_library(fms ALIAS FMS::fms_r8)\n"
    )
    changed = cli._apply_fv3_fms_required_fallback(repo)
    assert changed is True
    text = cmake.read_text()
    assert "find_package(FMS REQUIRED)" in text
    assert "FMS::fms_r8" not in text
    assert "if(TARGET FMS::fms)" in text
    assert "add_library(fms ALIAS FMS::fms)" in text


def test_apply_fv3_fms_required_fallback_guards_generic_alias_form(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "fv3" / "atmos_cubed_sphere" / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text(
        "find_package(FMS COMPONENTS R4 REQUIRED)\n"
        "add_library(fms ALIAS FMS::${FMS_TARGET})\n"
    )
    changed = cli._apply_fv3_fms_required_fallback(repo)
    assert changed is True
    text = cmake.read_text()
    assert "find_package(FMS REQUIRED)" in text
    assert "add_library(fms ALIAS FMS::${FMS_TARGET})" not in text
    assert "if(TARGET FMS::fms)" in text


def test_apply_fv3_fms_required_fallback_collapses_repeated_alias_guards(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "fv3" / "atmos_cubed_sphere" / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text(
        "if(NOT FMS_FOUND)\n"
        "  find_package(FMS REQUIRED)\n"
        "  string(TOLOWER ${kind} kind_lower)\n"
        "  if(TARGET FMS::fms)\n"
        "  if(TARGET FMS::fms)\n"
        "  add_library(fms ALIAS FMS::fms)\n"
        "endif()\n"
        "endif()\n"
        "endif()\n"
        "list(APPEND model_srcs a.F90)\n"
    )
    changed = cli._apply_fv3_fms_required_fallback(repo)
    assert changed is True
    text = cmake.read_text()
    assert text.count("if(TARGET FMS::fms)") == 1
    assert text.count("add_library(fms ALIAS FMS::fms)") == 1


def test_apply_fv3_top_level_dependency_guards(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text("add_dependencies(ufsatm_fv3 stochastic_physics)\n")
    changed = cli._apply_fv3_top_level_dependency_guards(repo)
    assert changed is True
    text = cmake.read_text()
    assert "stochastic_physics" not in text


def test_apply_fv3_top_level_dependency_guards_with_multiple_dependencies(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text("add_dependencies(ufsatm_fv3 a b stochastic_physics c)\n")
    changed = cli._apply_fv3_top_level_dependency_guards(repo)
    assert changed is True
    text = cmake.read_text()
    assert "add_dependencies(ufsatm_fv3 a b c)" in text
    assert "stochastic_physics" not in text


def test_apply_fv3_top_level_dependency_guards_strips_link_library(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text("target_link_libraries(ufsatm_fv3 PUBLIC a stochastic_physics b)\n")
    changed = cli._apply_fv3_top_level_dependency_guards(repo)
    assert changed is True
    text = cmake.read_text()
    assert "stochastic_physics" not in text
    assert "target_link_libraries(ufsatm_fv3 PUBLIC a b)" in text


def test_apply_fv3_top_level_dependency_guards_variable_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text("add_dependencies(${FV3_TARGET} x stochastic_physics y)\n")
    changed = cli._apply_fv3_top_level_dependency_guards(repo)
    assert changed is True
    text = cmake.read_text()
    assert "add_dependencies(${FV3_TARGET} x y)" in text
    assert "stochastic_physics" not in text


def test_apply_fv3_top_level_dependency_guards_removes_empty_if_block(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    cmake = repo / "CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text(
        "add_dependencies(${DYCORE_TARGET} fv3 fv3ccpp)\n"
        "if(TARGET stochastic_physics)\n"
        "\n"
        "endif()\n"
        "if(CDEPS_INLINE)\n"
        "  add_dependencies(${DYCORE_TARGET} cdeps::cdeps)\n"
        "endif()\n"
    )
    changed = cli._apply_fv3_top_level_dependency_guards(repo)
    assert changed is True
    text = cmake.read_text()
    assert "if(TARGET stochastic_physics)" not in text
    assert "add_dependencies(${DYCORE_TARGET} fv3 fv3ccpp)" in text


def test_apply_fv3_external_sst_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    f90 = repo / "fv3" / "atmos_cubed_sphere" / "tools" / "external_sst.F90"
    f90.parent.mkdir(parents=True)
    f90.write_text(
        "module external_sst_mod\n"
        "#else\n"
        "use amip_interp_mod, only: i_sst, j_sst, sst_ncep, sst_anom, &\n"
        "                           forecast_mode, use_ncep_sst\n"
        "#endif\n"
        "end module external_sst_mod\n"
    )
    changed = cli._apply_fv3_external_sst_fallback(repo)
    assert changed is True
    text = f90.read_text()
    assert "use amip_interp_mod, only: i_sst, j_sst, forecast_mode, use_ncep_sst" in text
    assert "real, allocatable, dimension(:,:) ::  sst_ncep, sst_anom" in text


def test_apply_fv3_stochastic_wrapper_stub(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    f90 = repo / "fv3" / "stochastic_physics" / "stochastic_physics_wrapper.F90"
    f90.parent.mkdir(parents=True)
    f90.write_text("module stochastic_physics_wrapper_mod\nend module stochastic_physics_wrapper_mod\n")
    changed = cli._apply_fv3_stochastic_wrapper_stub(repo)
    assert changed is True
    text = f90.read_text()
    assert "NORAA_FV3_STOCH_STUB" in text
    assert "subroutine stochastic_physics_wrapper" in text


def test_apply_fv3_update_ca_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    f90 = repo / "fv3" / "atmos_model.F90"
    f90.parent.mkdir(parents=True)
    f90.write_text(
        "  use update_ca, only: read_ca_restart\n"
        "  use update_ca, only: write_ca_restart\n"
        "  use get_stochy_pattern_mod, only: write_stoch_restart_atm\n"
        "      call read_ca_restart(a,b,c)\n"
        "       call write_ca_restart(ts)\n"
    )
    changed = cli._apply_fv3_update_ca_fallback(repo)
    assert changed is True
    text = f90.read_text()
    assert "NORAA_FV3_UPDATE_CA_FALLBACK" in text
    assert "use update_ca" not in text
    assert "use get_stochy_pattern_mod" not in text
    assert "call read_ca_restart" not in text
    assert "call write_ca_restart" not in text


def test_apply_fv3_stochy_pattern_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    fv3 = repo / "fv3"
    fv3.mkdir(parents=True)
    f1 = fv3 / "module_fcst_grid_comp.F90"
    f1.write_text(
        "  use get_stochy_pattern_mod, only: write_stoch_restart_atm\n"
        "          call write_stoch_restart_atm('x')\n"
    )
    changed = cli._apply_fv3_stochy_pattern_fallback(repo)
    assert changed is True
    text = f1.read_text()
    assert "NORAA_FV3_STOCHY_PATTERN_FALLBACK" in text
    assert "use get_stochy_pattern_mod" not in text
    assert "call write_stoch_restart_atm" not in text


def test_apply_fv3_fv_dynamics_kind_fix(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    f90 = repo / "fv3" / "atmos_cubed_sphere" / "model" / "fv_dynamics.F90"
    f90.parent.mkdir(parents=True)
    f90.write_text(
        "      real(8), dimension(:,:,:), pointer :: cappa\n"
        "      real(kind=kind_phys), dimension(:,:,:), pointer :: dp1\n"
        "      real(kind=kind_dyn), pointer, dimension(:,:,:) :: dtdt_m\n"
        "      real(4), pointer, dimension(:,:) :: te_2d\n"
    )
    changed = cli._apply_fv3_fv_dynamics_kind_fix(repo)
    assert changed is True
    text = f90.read_text()
    assert "real, dimension(:,:,:), pointer :: cappa" in text
    assert "real, dimension(:,:,:), pointer :: dp1" in text
    assert "real, dimension(:,:,:), pointer :: dtdt_m" in text
    assert "real, dimension(:,:), pointer :: te_2d" in text
