"""Enforce use of the public beanbeaver.ledger_access API surface."""

from __future__ import annotations

import ast
from pathlib import Path


def test_runtime_code_uses_public_ledger_access_api_only() -> None:
    root = Path(__file__).resolve().parents[1]
    runtime_roots = [
        "application",
        "cli",
        "domain",
        "importers",
        "receipt",
        "runtime",
        "util",
    ]

    violations: list[str] = []
    for top in runtime_roots:
        for path in sorted((root / top).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module in {"beanbeaver.ledger_access.reader", "beanbeaver.ledger_access.writer"}:
                        violations.append(f"{path.relative_to(root)}: direct import from {module}")
                    if module == "beanbeaver.ledger_access":
                        for alias in node.names:
                            if alias.name in {"get_ledger_reader", "get_ledger_writer"}:
                                violations.append(
                                    f"{path.relative_to(root)}: imports deprecated ledger_access symbol {alias.name}"
                                )

    assert not violations, "ledger_access API boundary violations:\n" + "\n".join(violations)
