from __future__ import annotations

from pathlib import Path

import pytest

from noraa.buildsystem.paths import (
    bootstrapped_deps_prefix,
    bootstrapped_esmf_mk,
    detect_verify_script,
    pick_mpas_suite,
    resolve_deps_prefix,
    resolve_esmf_mkfile,
)


def test_detect_verify_script_prefers_mpas_smoke(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    verify_smoke = scripts / "verify_mpas_smoke.sh"
    verify_generic = scripts / "verify.sh"
    verify_smoke.write_text("#!/usr/bin/env bash\n")
    verify_generic.write_text("#!/usr/bin/env bash\n")

    found = detect_verify_script(tmp_path)
    assert found == verify_smoke


def test_pick_mpas_suite_prefers_rrfs(tmp_path: Path) -> None:
    suites = tmp_path / "ccpp" / "suites"
    suites.mkdir(parents=True)
    (suites / "suite_MPAS_RRFS.xml").write_text("<suite/>")
    (suites / "suite_MPAS_FOO.xml").write_text("<suite/>")

    assert pick_mpas_suite(tmp_path) == "suite_MPAS_RRFS"


def test_pick_mpas_suite_falls_back_to_any_mpas_suite(tmp_path: Path) -> None:
    suites = tmp_path / "ccpp" / "suites"
    suites.mkdir(parents=True)
    (suites / "suite_MPAS_ALPHA.xml").write_text("<suite/>")

    assert pick_mpas_suite(tmp_path) == "suite_MPAS_ALPHA"


def test_pick_mpas_suite_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        pick_mpas_suite(tmp_path)
    assert "Missing ccpp/suites" in str(excinfo.value)


def test_bootstrapped_path_helpers(tmp_path: Path) -> None:
    deps_install = tmp_path / ".noraa" / "deps" / "install"
    deps_install.mkdir(parents=True)
    esmf_mk = tmp_path / ".noraa" / "esmf" / "install" / "lib" / "esmf.mk"
    esmf_mk.parent.mkdir(parents=True)
    esmf_mk.write_text("ESMF")

    assert bootstrapped_deps_prefix(tmp_path) == deps_install
    assert bootstrapped_esmf_mk(tmp_path) == esmf_mk


def test_resolve_deps_prefix_prefers_explicit(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit_deps"
    explicit.mkdir()
    boot = tmp_path / ".noraa" / "deps" / "install"
    boot.mkdir(parents=True)

    assert resolve_deps_prefix(tmp_path, str(explicit)) == str(explicit)


def test_resolve_deps_prefix_uses_bootstrapped(tmp_path: Path) -> None:
    boot = tmp_path / ".noraa" / "deps" / "install"
    boot.mkdir(parents=True)

    assert resolve_deps_prefix(tmp_path, None) == str(boot)


def test_resolve_esmf_mkfile_prefers_explicit(tmp_path: Path) -> None:
    explicit = tmp_path / "external" / "esmf.mk"
    explicit.parent.mkdir(parents=True)
    explicit.write_text("ESMF")

    value = resolve_esmf_mkfile(tmp_path, deps_prefix=None, esmf_mkfile=str(explicit))
    assert value == str(explicit)


def test_resolve_esmf_mkfile_uses_bootstrapped(tmp_path: Path) -> None:
    boot = tmp_path / ".noraa" / "esmf" / "install" / "lib" / "esmf.mk"
    boot.parent.mkdir(parents=True)
    boot.write_text("ESMF")

    value = resolve_esmf_mkfile(tmp_path, deps_prefix=None, esmf_mkfile=None)
    assert value == str(boot)


def test_resolve_esmf_mkfile_uses_deps_prefix(tmp_path: Path) -> None:
    deps = tmp_path / "deps"
    mk = deps / "lib" / "esmf.mk"
    mk.parent.mkdir(parents=True)
    mk.write_text("ESMF")

    value = resolve_esmf_mkfile(tmp_path, deps_prefix=str(deps), esmf_mkfile=None)
    assert value == str(mk)


def test_resolve_esmf_mkfile_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        resolve_esmf_mkfile(tmp_path, deps_prefix=None, esmf_mkfile=None)
    assert "ESMF not found" in str(excinfo.value)

