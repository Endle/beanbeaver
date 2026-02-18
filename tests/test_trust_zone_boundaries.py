"""Trust-zone dependency rules enforced from docs/trust_zone.md."""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "trust_zone.md"
_ZONE_NAMES = {"Privileged", "Orchestrator", "Pure"}
_ALLOWED_TARGET_ZONES = {
    "Privileged": {"Privileged", "Pure"},
    "Orchestrator": {"Privileged", "Orchestrator", "Pure"},
    "Pure": {"Pure"},
}


def _normalize_zone_path(raw: str) -> tuple[str, ...]:
    cleaned = raw.strip().strip("/")
    cleaned = re.sub(r"^vendor/beanbeaver/", "", cleaned)
    return tuple(part for part in cleaned.split("/") if part)


def _parse_zone_mapping() -> dict[str, list[tuple[str, ...]]]:
    text = _DOC.read_text(encoding="utf-8")
    mapping: dict[str, list[tuple[str, ...]]] = {zone: [] for zone in _ZONE_NAMES}

    in_mapping = False
    current_zone: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "Current Directory Mapping":
            in_mapping = True
            continue
        if not in_mapping:
            continue
        if stripped in {
            "Dependency Rules",
            "Inheritance Rules",
            "Contributor Checklist",
        }:
            break

        token_match = re.match(r"^\s*-\s+`([^`]+)`", line)
        if not token_match:
            continue
        token = token_match.group(1).strip()

        if token in _ZONE_NAMES:
            current_zone = token
            continue
        if current_zone is None:
            continue

        path_parts = _normalize_zone_path(token)
        if path_parts:
            mapping[current_zone].append(path_parts)

    return mapping


def _zone_for_parts(
    parts: tuple[str, ...],
    zone_entries: list[tuple[tuple[str, ...], str]],
) -> str | None:
    for prefix, zone in zone_entries:
        if len(parts) >= len(prefix) and parts[: len(prefix)] == prefix:
            return zone
    return None


def _module_name_for_file(path: Path) -> str:
    rel_no_suffix = path.relative_to(_ROOT).with_suffix("")
    parts = list(rel_no_suffix.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["beanbeaver", *parts])


def _imported_modules(path: Path) -> list[str]:
    module_name = _module_name_for_file(path)
    current_package = (
        module_name if path.name == "__init__.py" else module_name.rsplit(".", 1)[0]
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    imports.append(node.module)
                continue

            rel_name = "." * node.level + (node.module or "")
            try:
                imports.append(importlib.util.resolve_name(rel_name, current_package))
            except ImportError:
                continue
    return imports


def test_trust_zone_doc_paths_exist() -> None:
    mapping = _parse_zone_mapping()
    missing: list[str] = []

    for zone, paths in mapping.items():
        assert paths, f"Missing zone mapping for {zone} in {_DOC}"
        for parts in paths:
            path = _ROOT / Path(*parts)
            if not path.exists():
                missing.append(str(path.relative_to(_ROOT)))

    assert not missing, "Trust-zone paths in docs do not exist:\n" + "\n".join(
        sorted(missing)
    )


def test_trust_zone_import_boundaries() -> None:
    mapping = _parse_zone_mapping()
    zone_entries: list[tuple[tuple[str, ...], str]] = []
    runtime_files: set[Path] = set()

    for zone, paths in mapping.items():
        for parts in paths:
            zone_entries.append((parts, zone))
            dir_path = _ROOT / Path(*parts)
            if dir_path.exists():
                runtime_files.update(dir_path.rglob("*.py"))

    zone_entries.sort(key=lambda item: len(item[0]), reverse=True)
    violations: list[str] = []

    for path in sorted(runtime_files):
        rel = path.relative_to(_ROOT)
        source_zone = _zone_for_parts(rel.parts, zone_entries)
        if source_zone is None:
            continue

        for module in _imported_modules(path):
            if not module.startswith("beanbeaver."):
                continue

            target_parts = tuple(part for part in module.split(".")[1:] if part)
            if not target_parts:
                continue
            target_zone = _zone_for_parts(target_parts, zone_entries)
            if target_zone is None:
                continue

            if target_zone not in _ALLOWED_TARGET_ZONES[source_zone]:
                violations.append(
                    f"{rel}: {source_zone} imports {module} ({target_zone})"
                )

    assert not violations, "Trust-zone import violations:\n" + "\n".join(violations)
