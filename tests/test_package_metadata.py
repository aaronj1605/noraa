from __future__ import annotations

from pathlib import Path


def test_pyproject_requires_python_311_plus() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.11"' in text