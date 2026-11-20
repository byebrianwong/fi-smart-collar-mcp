import pytest

from fi_mcp import setup_cli
from fi_mcp.client import FiLoginError


def _fake_prompts(monkeypatch, emails, passwords, login_results):
    """Feed answers to input()/getpass() and fake each login attempt."""
    emails, passwords, results = iter(emails), iter(passwords), iter(login_results)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(emails))
    monkeypatch.setattr(setup_cli.getpass, "getpass", lambda prompt="": next(passwords))
    tried = []

    async def fake_try_login(email, password):
        tried.append((email, password))
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(setup_cli, "_try_login", fake_try_login)
    return tried


def test_retry_after_wrong_password_keeps_email(monkeypatch, capsys):
    tried = _fake_prompts(
        monkeypatch,
        emails=["me@example.com", ""],  # blank second answer keeps the first email
        passwords=["wrong", "right"],
        login_results=[FiLoginError("Fi says the email or password is wrong (HTTP 401)."), ["Biscuit"]],
    )
    assert setup_cli._ask_until_login_works() == ("me@example.com", "right", ["Biscuit"])
    assert tried == [("me@example.com", "wrong"), ("me@example.com", "right")]
    assert "Forgot password" in capsys.readouterr().out


def test_gives_up_after_three_wrong_logins(monkeypatch):
    wrong = FiLoginError("Fi says the email or password is wrong (HTTP 401).")
    _fake_prompts(monkeypatch, ["me@example.com", "", ""], ["a", "b", "c"], [wrong, wrong, wrong])
    with pytest.raises(SystemExit, match="Nothing was saved"):
        setup_cli._ask_until_login_works()
