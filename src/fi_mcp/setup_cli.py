"""`fi-mcp-setup`: save your Fi login to the macOS Keychain.

    uv run fi-mcp-setup           # ask for email and password, test them, save
    uv run fi-mcp-setup --check   # log in with the saved login and list pets
    uv run fi-mcp-setup --clear   # delete the saved login

The password is typed at a hidden prompt and goes only to Fi's login endpoint
and the Keychain.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from . import credentials, queries
from .client import FiClient, FiError, FiLoginError


async def _try_login(email: str, password: str) -> list[str]:
    client = FiClient(email, password, cache_seconds=0)
    try:
        await client.login()
        data = await client.query(queries.HOUSEHOLD, use_cache=False)
    finally:
        await client.aclose()
    names = []
    for membership in data["currentUser"]["userHouseholds"]:
        for pet in membership["household"]["pets"]:
            names.append(pet["name"] + ("" if pet.get("device") else " (no collar)"))
    return names


MAX_TRIES = 3

WRONG_LOGIN_HINT = """\
Things to check:
  - Use the email and password you use in the Fi phone app.
  - If you sign in to the Fi app with Apple or Google, your account may
    have no Fi password. Set one with "Forgot password" on the Fi app's
    sign-in screen, then try again here.
  - The password prompt shows nothing as you type. Pasting works."""


def _ask_until_login_works() -> tuple[str, str, list[str]]:
    """Prompt for email and password, test them, and retry on a wrong login."""
    email = ""
    for attempt in range(1, MAX_TRIES + 1):
        email = input(f"Fi account email [{email}]: " if email else "Fi account email: ").strip() or email
        password = getpass.getpass("Fi password (hidden): ")
        if not email or not password:
            print("Email and password are both required.")
            continue
        try:
            return email, password, asyncio.run(_try_login(email, password))
        except FiLoginError as exc:
            print(str(exc))
            if attempt == 1:
                print(WRONG_LOGIN_HINT)
            if attempt < MAX_TRIES:
                print(f"\nTry again ({MAX_TRIES - attempt} left). Press Return to keep the same email.")
        except (FiError, OSError) as exc:
            sys.exit(f"Could not reach Fi: {exc}\nNothing was saved.")
    sys.exit("Nothing was saved.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="fi-mcp-setup", description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="test the saved login")
    parser.add_argument("--clear", action="store_true", help="delete the saved login")
    args = parser.parse_args()

    if args.clear:
        removed = credentials.clear()
        print("Deleted the saved Fi login." if removed else "No saved Fi login found.")
        return

    if args.check:
        try:
            email, password = credentials.load()
        except credentials.MissingCredentials as exc:
            sys.exit(str(exc))
        try:
            pets = asyncio.run(_try_login(email, password))
        except FiLoginError as exc:
            sys.exit(f"{exc}\nRun `uv run fi-mcp-setup` to save a new login.")
        except (FiError, OSError) as exc:
            sys.exit(f"Could not reach Fi: {exc}")
    else:
        email, password, pets = _ask_until_login_works()

    if not args.check:
        credentials.save(email, password)
        print("Login works. Saved to the macOS Keychain (service: fi-collar-mcp).")
    else:
        print("Saved login works.")
    print("Pets on this account: " + (", ".join(pets) if pets else "none found"))


if __name__ == "__main__":
    main()
