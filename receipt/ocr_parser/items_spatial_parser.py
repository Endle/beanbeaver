"""Spatial (bbox-based) receipt item extraction via the native backend."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from beanbeaver.domain.receipt import ReceiptItem, ReceiptWarning

from .._rust import load_rust_matcher, require_rust_matcher
from ..item_categories import ItemCategoryRuleLayers, categorize_item

_SCALE_FACTOR = Decimal("10000")

_rust_matcher = load_rust_matcher()


def _select_spatial_item_line(
    price_y: float,
    candidates: list[dict[str, Any]],
    *,
    prefer_below: bool,
    price_line_has_onsale: bool,
) -> tuple[int, float] | None:
    result = require_rust_matcher().select_spatial_item_line(
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
    native_items, native_warnings = require_rust_matcher().extract_spatial_items(pages)

    if warning_sink is not None:
        warning_sink.extend(
            ReceiptWarning(
                message=message,
                after_item_index=after_item_index,
            )
            for message, after_item_index in native_warnings
        )

    return [
        ReceiptItem(
            description=description,
            price=Decimal(price_scaled) / _SCALE_FACTOR,
            category=categorize_item(description, rule_layers=item_category_rule_layers),
        )
        for description, price_scaled in native_items
    ]
