"""Public smoke tests for basic module wiring.

Keep these minimal and free of any real-world data.
"""

from __future__ import annotations

import importlib
import importlib.abc
import sys

import pytest


def test_imports() -> None:
    import beanbeaver
    import beanbeaver.cli.main
    import beanbeaver.importers
    import beanbeaver.receipt
    import beanbeaver.runtime

    assert beanbeaver is not None
    assert beanbeaver.cli.main is not None
    assert beanbeaver.importers is not None
    assert beanbeaver.receipt is not None
    assert beanbeaver.runtime is not None


class _BlockCsvRoutingFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> object:
        if fullname == "beanbeaver.application.imports.csv_routing":
            raise ImportError("csv_routing import blocked for smoke test")
        return None


def test_cli_help_does_not_require_csv_routing_import() -> None:
    finder = _BlockCsvRoutingFinder()
    sys.modules.pop("beanbeaver.cli.main", None)
    sys.modules.pop("beanbeaver.application.imports.csv_routing", None)
    sys.meta_path.insert(0, finder)

    try:
        cli_main = importlib.import_module("beanbeaver.cli.main")
        with pytest.raises(SystemExit) as exc_info:
            cli_main.main(["--help"])
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop("beanbeaver.cli.main", None)

    assert exc_info.value.code == 0
