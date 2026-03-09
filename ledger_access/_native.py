"""Native backend loader for ledger access."""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import site
import sys
from pathlib import Path
from types import ModuleType


def _load_extension_module(candidate: Path) -> ModuleType | None:
    loader = importlib.machinery.ExtensionFileLoader("beanbeaver._rust_matcher", str(candidate))
    spec = importlib.util.spec_from_file_location("beanbeaver._rust_matcher", candidate, loader=loader)
    if spec is None:
        return None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _candidate_patterns() -> list[str]:
    suffixes = list(importlib.machinery.EXTENSION_SUFFIXES)
    if ".dylib" not in suffixes:
        suffixes.append(".dylib")
    if ".dll" not in suffixes:
        suffixes.append(".dll")

    patterns: list[str] = []
    for stem in ("_rust_matcher", "lib_rust_matcher"):
        for suffix in suffixes:
            patterns.append(f"{stem}*{suffix}")
    return patterns


def _candidate_directories(project_root: Path) -> list[Path]:
    directories = [
        project_root / "target" / "maturin",
        project_root / "target" / "debug",
        project_root / "target" / "release",
        project_root / "target",
    ]
    directories.extend(Path(base) for base in site.getsitepackages())
    directories.extend(Path(base) for base in sys.path if base)

    ordered: list[Path] = []
    seen: set[Path] = set()
    for directory in directories:
        try:
            resolved = directory.resolve()
        except OSError:
            resolved = directory
        if resolved in seen or not directory.exists():
            continue
        seen.add(resolved)
        ordered.append(directory)
    return ordered


def load_native_backend() -> ModuleType:
    """Load the PyO3 extension, raising if it cannot be found."""
    for module_name in ("beanbeaver._rust_matcher", "_rust_matcher"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue

    project_root = Path(__file__).resolve().parents[1]
    site_roots = {Path(base) for base in site.getsitepackages()}
    for directory in _candidate_directories(project_root):
        matcher = directory.rglob if directory == project_root / "target" or directory in site_roots else directory.glob
        for pattern in _candidate_patterns():
            for candidate in sorted(matcher(pattern)):
                module = _load_extension_module(candidate)
                if module is not None:
                    return module

    raise ImportError("beanbeaver native extension module '_rust_matcher' is required but was not found")


_native_backend = load_native_backend()
