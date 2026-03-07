"""Privileged path helpers for ledger access modules."""

from __future__ import annotations

from pathlib import Path


def default_main_beancount_path() -> Path:
    """Resolve the default ``main.beancount`` path without runtime imports."""
    here = Path(__file__).resolve()

    # Prefer an existing ledger discovered by walking up from this module.
    for base in (here.parent, *here.parents):
        candidate = base / "main.beancount"
        if candidate.exists():
            return candidate

    # Otherwise anchor to the nearest project-like root.
    for base in (here.parent, *here.parents):
        if (base / "pyproject.toml").exists() or (base / ".git").exists():
            return (base / "main.beancount").resolve()

    # Final fallback for unusual layouts.
    return Path("main.beancount")
