"""Tender create+edit patch validation and application in the approval pipeline."""

from __future__ import annotations

import pytest
from beanbeaver.application.receipts.approval import (
    _apply_review_patches,
    _validate_tender_review_patches,
)


def test_validate_tender_create_patch_requires_amount_and_kind() -> None:
    with pytest.raises(ValueError, match="missing required 'amount'"):
        _validate_tender_review_patches(
            [{"create": True, "review": {"kind": "gift_card"}}],
            tender_count=0,
        )
    with pytest.raises(ValueError, match="missing required 'kind'"):
        _validate_tender_review_patches(
            [{"create": True, "review": {"amount": "25.00"}}],
            tender_count=0,
        )


def test_validate_tender_create_patch_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="is not one of"):
        _validate_tender_review_patches(
            [{"create": True, "review": {"amount": "25.00", "kind": "voucher"}}],
            tender_count=0,
        )


def test_validate_tender_create_patch_normalizes_optional_account_and_label() -> None:
    normalized = _validate_tender_review_patches(
        [
            {
                "create": True,
                "review": {
                    "amount": "25.00",
                    "kind": "gift_card",
                    "account": "Assets:Costco:ShopCard",
                    "raw_label": "SHOP CARD",
                },
            }
        ],
        tender_count=0,
    )

    assert normalized == [
        {
            "create": True,
            "tender": {
                "amount": "25.00",
                "kind": "gift_card",
                "account": "Assets:Costco:ShopCard",
                "raw_label": "SHOP CARD",
            },
        }
    ]


def test_apply_review_patches_appends_created_tender() -> None:
    document: dict = {"tenders": [{"amount": "441.68", "kind": "card"}]}

    _apply_review_patches(
        document,
        review_patch={},
        item_review_patches=[],
        tender_review_patches=[
            {
                "create": True,
                "tender": {"amount": "25.00", "kind": "gift_card"},
            }
        ],
    )

    assert document["tenders"] == [
        {"amount": "441.68", "kind": "card"},
        {
            "amount": "25.00",
            "kind": "gift_card",
            "meta": {"source": "tui_review"},
        },
    ]


def test_apply_review_patches_supports_edit_and_create_in_one_call() -> None:
    document: dict = {"tenders": [{"amount": "441.68", "kind": "card"}]}

    _apply_review_patches(
        document,
        review_patch={},
        item_review_patches=[],
        tender_review_patches=[
            {"index": 0, "review": {"account": "Liabilities:CC:Costco"}},
            {
                "create": True,
                "tender": {"amount": "25.00", "kind": "gift_card"},
            },
        ],
    )

    assert document["tenders"][0]["review"] == {"account": "Liabilities:CC:Costco"}
    assert document["tenders"][1]["amount"] == "25.00"
    assert document["tenders"][1]["kind"] == "gift_card"


def test_apply_review_patches_initializes_missing_tenders_list_for_create() -> None:
    document: dict = {}

    _apply_review_patches(
        document,
        review_patch={},
        item_review_patches=[],
        tender_review_patches=[
            {
                "create": True,
                "tender": {"amount": "25.00", "kind": "gift_card"},
            }
        ],
    )

    assert document["tenders"][0]["amount"] == "25.00"
