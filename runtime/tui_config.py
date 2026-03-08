"""Persistence helpers for the experimental TUI configuration."""

from __future__ import annotations

import json
from pathlib import Path

from beanbeaver.runtime.paths import get_paths


def _config_path() -> Path:
    return get_paths().config / "tui.json"


def load_tui_config() -> dict[str, str]:
    """Load TUI config from the current project root."""
    config_path = _config_path()
    if not config_path.exists():
        return {}

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(payload, dict):
        return {}

    result: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, str):
            result[key] = value
    return result


def save_tui_config(config: dict[str, str]) -> Path:
    """Write TUI config for the current project root."""
    config_path = _config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return config_path


def set_main_beancount_path(path: str) -> Path:
    """Persist the ledger path override used by the TUI and backend commands."""
    cleaned = path.strip()
    config = load_tui_config()
    if cleaned:
        config["main_beancount_path"] = cleaned
    else:
        config.pop("main_beancount_path", None)
    return save_tui_config(config)
