"""Architecture rules for modules under beanbeaver.util."""

from __future__ import annotations

import ast
from pathlib import Path


def test_util_modules_do_not_import_beancount_or_beanbeaver() -> None:
    util_dir = Path(__file__).resolve().parents[1] / "util"
    assert util_dir.exists(), f"Missing util directory: {util_dir}"

    violations: list[str] = []

    for path in sorted(util_dir.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name == "beancount" or name.startswith("beancount."):
                        violations.append(f"{path}: import {name}")
                    if name == "beanbeaver" or name.startswith("beanbeaver."):
                        violations.append(f"{path}: import {name}")

            if isinstance(node, ast.ImportFrom):
                module = node.module or ""

                # from beancount... / from beanbeaver...
                if module == "beancount" or module.startswith("beancount."):
                    violations.append(f"{path}: from {module} import ...")
                if module == "beanbeaver" or module.startswith("beanbeaver."):
                    violations.append(f"{path}: from {module} import ...")

                # Relative imports that escape util package are forbidden.
                # In util/*, level=1 means current package; level>1 escapes upward.
                if node.level and node.level > 1:
                    violations.append(f"{path}: relative import level {node.level} escapes util package")

    assert not violations, "Util import rule violations:\n" + "\n".join(violations)
