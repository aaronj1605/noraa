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
    assert "self-test" in result.stdout


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


def test_rt_help_has_guide() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["rt", "--help"])
    assert result.exit_code == 0
    assert "guide" in result.stdout
    assert "advanced-guide" in result.stdout
    assert "validate-case" in result.stdout


def test_rt_guide_help_has_core_option() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["rt", "guide", "--help"])
    assert result.exit_code == 0
    assert "--core" in result.stdout


def test_verify_help_uses_meaningful_metavars() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["verify", "--help"])
    assert result.exit_code == 0
    assert "REPO_PATH" in result.stdout
    assert "CORE" in result.stdout
    assert "DEPS_PREFIX" in result.stdout
    assert "ESMF_MKFILE" in result.stdout
