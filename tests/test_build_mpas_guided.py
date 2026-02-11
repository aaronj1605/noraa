from __future__ import annotations

import pytest

from noraa import cli


def test_confirm_or_fail_accepts_with_assume_yes() -> None:
    cli._confirm_or_fail(
        prompt="ignored",
        assume_yes=True,
        failure_message="should not fail",
        next_step="noraa init",
    )


def test_confirm_or_fail_raises_when_user_declines(monkeypatch) -> None:
    monkeypatch.setattr(cli.typer, "confirm", lambda *_args, **_kwargs: False)
    with pytest.raises(SystemExit) as excinfo:
        cli._confirm_or_fail(
            prompt="decline",
            assume_yes=False,
            failure_message="blocked",
            next_step="noraa init",
        )
    text = str(excinfo.value)
    assert "blocked" in text
    assert "Next step: noraa init" in text
