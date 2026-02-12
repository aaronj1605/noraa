from __future__ import annotations

from typer.testing import CliRunner

from noraa.cli import app


def test_help_shows_tested_baseline() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    flat = " ".join(result.stdout.split())
    assert "Tested baseline: Linux (Ubuntu 22.04/24.04), Python 3.11+, upstream ufsatm develop." in flat
    assert "Use: noraa <command> --help for command details." in flat


def test_run_smoke_fetch_data_help_is_command_oriented() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run-smoke", "fetch-data", "--help"])
    assert result.exit_code == 0
    assert "Commands" in result.stdout
    assert "scan" in result.stdout
    assert "official" in result.stdout
    assert "official-ufs" in result.stdout
    assert "official-regtests" in result.stdout
    assert "local" in result.stdout
    assert "clean-data" in result.stdout
    assert "--source" not in result.stdout


def test_run_smoke_help_has_validate_data() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run-smoke", "--help"])
    assert result.exit_code == 0
    assert "validate-data" in result.stdout
