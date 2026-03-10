"""Helpers for loading the optional native receipt extension."""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import site
import sys
from pathlib import Path
from types import ModuleType


def load_rust_matcher() -> ModuleType | None:
    for module_name in ("beanbeaver._rust_matcher", "_rust_matcher"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue

    project_root = Path(__file__).resolve().parents[1]
    suffixes = list(importlib.machinery.EXTENSION_SUFFIXES)
    if ".dylib" not in suffixes:
        suffixes.append(".dylib")
    if ".dll" not in suffixes:
        suffixes.append(".dll")
    patterns = [f"{stem}*{suffix}" for stem in ("_rust_matcher", "lib_rust_matcher") for suffix in suffixes]

    directories = [
        project_root / "target" / "maturin",
        project_root / "target" / "debug",
        project_root / "target" / "release",
        project_root / "target",
        *(Path(base) for base in site.getsitepackages()),
        *(Path(base) for base in sys.path if base),
    ]
    site_roots = {Path(base) for base in site.getsitepackages()}
    seen: set[Path] = set()
    for directory in directories:
        try:
            resolved = directory.resolve()
        except OSError:
            resolved = directory
        if resolved in seen or not directory.exists():
            continue
        seen.add(resolved)

        matcher = directory.rglob if directory == project_root / "target" or directory in site_roots else directory.glob
        for pattern in patterns:
            for candidate in sorted(matcher(pattern)):
                loader = importlib.machinery.ExtensionFileLoader("beanbeaver._rust_matcher", str(candidate))
                spec = importlib.util.spec_from_file_location("beanbeaver._rust_matcher", candidate, loader=loader)
                if spec is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                loader.exec_module(module)
                return module

    return None


def require_rust_matcher() -> ModuleType:
    module = load_rust_matcher()
    if module is None:
        raise ImportError("beanbeaver._rust_matcher is required for spatial receipt parsing")
    return module
