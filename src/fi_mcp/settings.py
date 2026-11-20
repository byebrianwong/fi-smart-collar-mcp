"""Per-dog settings that the owner writes by hand.

The file lives at ~/.config/fi-mcp/settings.toml, or at the path in the
FI_MCP_SETTINGS environment variable. Each dog gets a section named after the
dog (as it appears in Fi) or its Fi pet id:

    [dogs.Biscuit]
    collar_off_at_night = true

The file is read on every tool call, so an edit takes effect without
restarting the server. A missing file means no settings. A broken file is
logged and ignored, so a typo never stops the tools from working.

Settings:

- collar_off_at_night: the collar often comes off at night. Fi then records
  no night sleep and no night-time behavior, so the dog's rest looks low.
  The snapshot stops flagging rest for this dog and says why.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PATH = Path.home() / ".config" / "fi-mcp" / "settings.toml"
ENV_VAR = "FI_MCP_SETTINGS"


@dataclass(frozen=True)
class DogSettings:
    collar_off_at_night: bool = False


_FIELDS = {"collar_off_at_night"}


def path() -> Path:
    return Path(os.environ.get(ENV_VAR) or DEFAULT_PATH).expanduser()


def _load_all() -> dict[str, dict[str, Any]]:
    """Every [dogs.<name>] section, keyed by lowercased name or id."""
    file = path()
    try:
        with file.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("Ignoring settings file %s: %s", file, exc)
        return {}
    dogs = data.get("dogs")
    if not isinstance(dogs, dict):
        return {}
    return {str(name).strip().lower(): values for name, values in dogs.items() if isinstance(values, dict)}


def for_pet(pet: dict[str, Any]) -> DogSettings:
    """Settings for one dog, matched by Fi pet id or by name (any case)."""
    all_dogs = _load_all()
    values = all_dogs.get(str(pet.get("id") or "").lower()) or all_dogs.get((pet.get("name") or "").lower()) or {}
    unknown = set(values) - _FIELDS
    if unknown:
        log.warning("Unknown settings for %s ignored: %s", pet.get("name"), ", ".join(sorted(unknown)))
    return DogSettings(collar_off_at_night=values.get("collar_off_at_night") is True)
