"""Native backend loader for ledger access."""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
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


def load_native_backend() -> ModuleType:
    """Load the PyO3 extension, raising if it cannot be found."""
    for module_name in ("beanbeaver._rust_matcher", "_rust_matcher"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue

    project_root = Path(__file__).resolve().parents[1]
    for directory in (project_root / "target" / "maturin", project_root / "target" / "debug"):
        if not directory.exists():
            continue
        for pattern in (
            "_rust_matcher*.so",
            "lib_rust_matcher*.so",
            "_rust_matcher*.pyd",
            "lib_rust_matcher*.pyd",
            "_rust_matcher*.dylib",
            "lib_rust_matcher*.dylib",
        ):
            for candidate in sorted(directory.glob(pattern)):
                module = _load_extension_module(candidate)
                if module is not None:
                    return module

    raise ImportError("beanbeaver native extension module '_rust_matcher' is required but was not found")


_native_backend = load_native_backend()
