"""Spatial (bbox-based) receipt item extraction."""

import importlib
import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

from beanbeaver.domain.receipt import ReceiptItem, ReceiptWarning

from ..item_categories import ItemCategoryRuleLayers, categorize_item


def _load_rust_matcher() -> ModuleType | None:
    for module_name in ("beanbeaver._rust_matcher", "_rust_matcher"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue

    project_root = Path(__file__).resolve().parents[2]
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
                spec = importlib.util.spec_from_file_location("beanbeaver._rust_matcher", candidate)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module

    return None


_rust_matcher = _load_rust_matcher()
if _rust_matcher is None:
    raise ImportError("beanbeaver._rust_matcher is required for spatial receipt parsing")

_RUST_SCALE_FACTOR = Decimal("10000")


def _select_spatial_item_line(
    price_y: float,
    candidates: list[dict[str, Any]],
    *,
    prefer_below: bool,
    price_line_has_onsale: bool,
) -> tuple[int, float] | None:
    result = _rust_matcher.select_spatial_item_line(
        price_y,
        0.02,
        0.08,
        prefer_below,
        price_line_has_onsale,
        candidates,
    )
    if result is None:
        return None
    index, distance = result
    return int(index), float(distance)


def _extract_items_with_bbox(
    pages: list[dict[str, Any]],
    warning_sink: list[ReceiptWarning] | None = None,
    *,
    item_category_rule_layers: ItemCategoryRuleLayers,
) -> list[ReceiptItem]:
    raw_items, raw_warnings = _rust_matcher.extract_spatial_items(pages)

    items = [
        ReceiptItem(
            description=description,
            price=(Decimal(int(price_scaled)) / _RUST_SCALE_FACTOR),
            category=categorize_item(description, rule_layers=item_category_rule_layers),
        )
        for description, price_scaled in raw_items
    ]
    if warning_sink is not None:
        for message, after_item_index in raw_warnings:
            warning_sink.append(
                ReceiptWarning(
                    message=str(message),
                    after_item_index=None if after_item_index is None else int(after_item_index),
                )
            )
    return items
