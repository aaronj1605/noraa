from __future__ import annotations

from noraa.validate import validate_core_success


def test_validate_core_success_fv3_missing(tmp_path) -> None:
    out = tmp_path / ".noraa" / "logs" / "x"
    out.mkdir(parents=True)
    result = validate_core_success(tmp_path, deps_prefix=None, out_dir=out, core="fv3")
    assert result.ok is False
    assert "FV3 executable missing" in result.reason
