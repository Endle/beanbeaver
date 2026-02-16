"""Architecture boundary checks for domain/runtime-rule-engine coupling."""

from __future__ import annotations

import ast
from pathlib import Path


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                result.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            result.append(base)
    return result


def test_domain_does_not_import_runtime_rule_engine() -> None:
    domain_dir = Path(__file__).resolve().parents[1] / "domain"
    violations: list[str] = []
    for path in sorted(domain_dir.rglob("*.py")):
        for mod in _imports(path):
            if mod == "beanbeaver.runtime.rule_engine":
                violations.append(f"{path}: {mod}")
    assert not violations, "Domain->Runtime rule-engine import violations:\n" + "\n".join(violations)


def test_runtime_rule_engine_does_not_import_domain_package() -> None:
    rule_engine_path = Path(__file__).resolve().parents[1] / "runtime" / "rule_engine.py"
    violations: list[str] = []
    for mod in _imports(rule_engine_path):
        if mod == "beanbeaver.domain" or mod.startswith("beanbeaver.domain."):
            violations.append(f"{rule_engine_path}: {mod}")
    assert not violations, "Runtime rule-engine -> Domain import violations:\n" + "\n".join(violations)
