from __future__ import annotations

from pathlib import Path


def render_find_deps_script(
    *,
    repo_root: Path,
    lib_dir: Path,
    include_4: Path,
    include_d: Path,
    include_generic: Path,
    netcdf_lib: Path | None,
    netcdff_lib: Path | None,
    fms_lib: Path,
    fms_include: str,
) -> str:
    return (
        f"if(NOT EXISTS \"{lib_dir / 'libbacio_4.a'}\")\n"
        f"  message(FATAL_ERROR \"Missing dependency: {lib_dir / 'libbacio_4.a'}\")\n"
        "endif()\n"
        f"if(NOT EXISTS \"{lib_dir / 'libsp_d.a'}\")\n"
        f"  message(FATAL_ERROR \"Missing dependency: {lib_dir / 'libsp_d.a'}\")\n"
        "endif()\n"
        f"if(NOT EXISTS \"{lib_dir / 'libw3emc_d.a'}\")\n"
        f"  message(FATAL_ERROR \"Missing dependency: {lib_dir / 'libw3emc_d.a'}\")\n"
        "endif()\n"
        f"if(NOT EXISTS \"{lib_dir / 'libpiof.a'}\")\n"
        f"  message(FATAL_ERROR \"Missing dependency: {lib_dir / 'libpiof.a'}\")\n"
        "endif()\n"
        f"if(NOT EXISTS \"{lib_dir / 'libpioc.a'}\")\n"
        f"  message(FATAL_ERROR \"Missing dependency: {lib_dir / 'libpioc.a'}\")\n"
        "endif()\n"
        f"if(NOT EXISTS \"{fms_lib}\")\n"
        f"  message(FATAL_ERROR \"Missing dependency: {fms_lib}\")\n"
        "endif()\n"
        "if(NOT TARGET bacio::bacio_4)\n"
        "  add_library(bacio::bacio_4 STATIC IMPORTED GLOBAL)\n"
        f"  set_target_properties(bacio::bacio_4 PROPERTIES IMPORTED_LOCATION \"{lib_dir / 'libbacio_4.a'}\" INTERFACE_INCLUDE_DIRECTORIES \"{include_4}\")\n"
        "endif()\n"
        "if(NOT TARGET bufr::bufr_4)\n"
        "  add_library(bufr::bufr_4 STATIC IMPORTED GLOBAL)\n"
        f"  set_target_properties(bufr::bufr_4 PROPERTIES IMPORTED_LOCATION \"{lib_dir / 'libbufr_4.a'}\" INTERFACE_INCLUDE_DIRECTORIES \"{include_4}\")\n"
        "endif()\n"
        "if(NOT TARGET sp::sp_d)\n"
        "  add_library(sp::sp_d STATIC IMPORTED GLOBAL)\n"
        f"  set_target_properties(sp::sp_d PROPERTIES IMPORTED_LOCATION \"{lib_dir / 'libsp_d.a'}\")\n"
        "endif()\n"
        "if(NOT TARGET w3emc::w3emc_d)\n"
        "  add_library(w3emc::w3emc_d STATIC IMPORTED GLOBAL)\n"
        f"  set_target_properties(w3emc::w3emc_d PROPERTIES IMPORTED_LOCATION \"{lib_dir / 'libw3emc_d.a'}\" INTERFACE_INCLUDE_DIRECTORIES \"{include_d}\")\n"
        "  target_link_libraries(w3emc::w3emc_d INTERFACE bacio::bacio_4 bufr::bufr_4)\n"
        "endif()\n"
        "if(NOT TARGET PIO::PIO_C)\n"
        "  add_library(PIO::PIO_C STATIC IMPORTED GLOBAL)\n"
        f"  set_target_properties(PIO::PIO_C PROPERTIES IMPORTED_LOCATION \"{lib_dir / 'libpioc.a'}\" INTERFACE_INCLUDE_DIRECTORIES \"{include_generic}\")\n"
        f"  target_link_libraries(PIO::PIO_C INTERFACE {str(netcdf_lib) if netcdf_lib else 'netcdf'})\n"
        "endif()\n"
        "if(NOT TARGET PIO::PIO_Fortran)\n"
        "  add_library(PIO::PIO_Fortran STATIC IMPORTED GLOBAL)\n"
        f"  set_target_properties(PIO::PIO_Fortran PROPERTIES IMPORTED_LOCATION \"{lib_dir / 'libpiof.a'}\" INTERFACE_INCLUDE_DIRECTORIES \"{include_generic}\")\n"
        "  target_link_libraries(PIO::PIO_Fortran INTERFACE PIO::PIO_C)\n"
        "endif()\n"
        "if(NOT TARGET fms)\n"
        "  add_library(fms STATIC IMPORTED GLOBAL)\n"
        f"  set_target_properties(fms PROPERTIES IMPORTED_LOCATION \"{fms_lib}\" INTERFACE_INCLUDE_DIRECTORIES \"{fms_include}\")\n"
        "endif()\n"
        "if(NOT TARGET NetCDF::NetCDF_Fortran)\n"
        "  add_library(NetCDF::NetCDF_Fortran INTERFACE IMPORTED GLOBAL)\n"
        f"  set_target_properties(NetCDF::NetCDF_Fortran PROPERTIES INTERFACE_INCLUDE_DIRECTORIES \"{include_generic};/usr/include\")\n"
        f"  target_link_libraries(NetCDF::NetCDF_Fortran INTERFACE {str(netcdff_lib) if netcdff_lib else 'netcdff'} {str(netcdf_lib) if netcdf_lib else 'netcdf'})\n"
        "endif()\n"
        "if(NOT TARGET PnetCDF::PnetCDF_Fortran)\n"
        "  add_library(PnetCDF::PnetCDF_Fortran INTERFACE IMPORTED GLOBAL)\n"
        "  target_link_libraries(PnetCDF::PnetCDF_Fortran INTERFACE pnetcdf)\n"
        "endif()\n"
        "if(NOT TARGET MPI::MPI_Fortran)\n"
        "  add_library(MPI::MPI_Fortran INTERFACE IMPORTED GLOBAL)\n"
        "  target_link_libraries(MPI::MPI_Fortran INTERFACE mpi_usempif08 mpi_usempi_ignore_tkr mpi_mpifh mpi)\n"
        "endif()\n"
        f"set_source_files_properties(\"{repo_root / 'ccpp/physics/physics/hooks/machine.F'}\" PROPERTIES Fortran_FORMAT FREE COMPILE_FLAGS \"-ffree-form\")\n"
    )
