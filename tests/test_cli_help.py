from __future__ import annotations

from typer.testing import CliRunner

from noraa.cli import app


def test_help_shows_tested_baseline() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Tested baseline: Linux (Ubuntu 22.04/24.04), Python 3.11+, upstream ufsatm develop." in result.stdout
    assert "Use: noraa <command> --help for command details." in result.stdout