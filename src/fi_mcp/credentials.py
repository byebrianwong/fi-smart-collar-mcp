"""Where the Fi email and password come from.

Order of lookup:
1. The FI_EMAIL and FI_PASSWORD environment variables, if both are set.
2. The macOS Keychain (through the `keyring` package), filled in by
   running `uv run fi-mcp-setup`.

The password is never written to a file in this project.
"""

from __future__ import annotations

import os

import keyring

SERVICE = "fi-collar-mcp"
# Keychain entry that holds the email address. The password entry is stored
# under the email address itself.
_EMAIL_KEY = "__email__"


class MissingCredentials(RuntimeError):
    pass


def load() -> tuple[str, str]:
    email = os.environ.get("FI_EMAIL")
    password = os.environ.get("FI_PASSWORD")
    if email and password:
        return email, password

    email = email or keyring.get_password(SERVICE, _EMAIL_KEY)
    if email:
        password = keyring.get_password(SERVICE, email)
    if not email or not password:
        raise MissingCredentials(
            "No Fi login found. Run `uv run fi-mcp-setup` in the fi-mcp project "
            "folder to save your Fi email and password to the macOS Keychain, "
            "or set FI_EMAIL and FI_PASSWORD."
        )
    return email, password


def save(email: str, password: str) -> None:
    old_email = keyring.get_password(SERVICE, _EMAIL_KEY)
    if old_email and old_email != email:
        _delete_quietly(old_email)
    keyring.set_password(SERVICE, _EMAIL_KEY, email)
    keyring.set_password(SERVICE, email, password)


def clear() -> bool:
    email = keyring.get_password(SERVICE, _EMAIL_KEY)
    if not email:
        return False
    _delete_quietly(email)
    _delete_quietly(_EMAIL_KEY)
    return True


def _delete_quietly(key: str) -> None:
    try:
        keyring.delete_password(SERVICE, key)
    except keyring.errors.PasswordDeleteError:
        pass
