"""YAML configuration loading (plan §10).

Two functions:
* ``load_config`` — read the YAML, return a dict.
* ``cfg_get`` — dotted-key lookup with a default, saves chained
  ``cfg.get("wan2gp", {}).get("default_steps", 4)`` in the bridges.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

__all__ = ["load_config", "cfg_get"]


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Search order: explicit > ./lazycomics_config.yaml > ~/.lazycomics_config.yaml.

    Returns the parsed YAML as a dict. Returns ``{}`` if no config file
    is found in the search order. An explicit ``config_path`` that
    doesn't exist raises :class:`FileNotFoundError` from the standard
    ``open()`` call.
    """
    if config_path is not None:
        path = Path(config_path)
    else:
        cwd = Path.cwd() / "lazycomics_config.yaml"
        home = Path.home() / ".lazycomics_config.yaml"
        if cwd.is_file():
            path = cwd
        elif home.is_file():
            path = home
        else:
            return {}

    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_MISSING = object()


def cfg_get(config: dict[str, Any], dotted_key: str, default: Any = _MISSING) -> Any:
    """Look up ``"wan2gp.default_steps"``-style keys in a nested dict.

    Returns ``default`` if any segment is missing or hits a non-dict.
    Raises :class:`KeyError` if the key is missing and no default was
    supplied. The sentinel lets ``cfg_get(cfg, "x", None)`` legitimately
    return ``None`` for missing keys.
    """
    cur: Any = config
    for part in dotted_key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            if default is _MISSING:
                raise KeyError(dotted_key)
            return default
    return cur
