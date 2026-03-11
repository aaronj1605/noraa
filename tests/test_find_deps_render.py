from __future__ import annotations

from pathlib import Path

from noraa.buildsystem.find_deps import render_find_deps_script


def _render(include_fms_shim: bool) -> str:
    base = Path("/tmp/repo")
    return render_find_deps_script(
        repo_root=base,
        lib_dir=base / "lib",
        include_4=base / "include_4",
        include_d=base / "include_d",
        include_generic=base / "include",
        netcdf_lib=base / "lib" / "libnetcdf.so",
        netcdff_lib=base / "lib" / "libnetcdff.so",
        fms_lib=base / "lib" / "libfms.a",
        fms_include=str(base / "include" / "fms"),
        include_fms_shim=include_fms_shim,
        include_stochastic_physics_stub=False,
    )


def test_render_find_deps_includes_fms_shim_when_enabled() -> None:
    text = _render(include_fms_shim=True)
    assert "if(NOT TARGET fms)" in text


def test_render_find_deps_omits_fms_shim_when_disabled() -> None:
    text = _render(include_fms_shim=False)
    assert "if(NOT TARGET fms)" not in text


def test_render_find_deps_includes_stochastic_stub_when_enabled() -> None:
    base = Path("/tmp/repo")
    text = render_find_deps_script(
        repo_root=base,
        lib_dir=base / "lib",
        include_4=base / "include_4",
        include_d=base / "include_d",
        include_generic=base / "include",
        netcdf_lib=base / "lib" / "libnetcdf.so",
        netcdff_lib=base / "lib" / "libnetcdff.so",
        fms_lib=base / "lib" / "libfms.a",
        fms_include=str(base / "include" / "fms"),
        include_fms_shim=False,
        include_stochastic_physics_stub=True,
    )
    assert "if(NOT TARGET stochastic_physics)" in text
    assert "add_library(stochastic_physics INTERFACE)" in text
