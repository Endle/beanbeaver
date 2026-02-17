"""Tests for receipt item category matching."""

from pathlib import Path

from beanbeaver.receipt.item_categories import categorize_item
from beanbeaver.runtime.item_category_rules import load_item_category_rule_layers

# TODO does this file has real usage?


def test_corn_oil_maps_to_seasoning() -> None:
    assert (
        categorize_item(
            "SAPORITO FOODS CORN OIL 2.84L",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Seasoning"
    )


def test_pericarpium_zanthoxyli_maps_to_seasoning() -> None:
    assert (
        categorize_item(
            "FLOWER PERICARPIURN ZANTHOXYLI",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Seasoning"
    )


def test_red_chili_pepper_maps_to_seasoning() -> None:
    assert (
        categorize_item(
            "T&T SLICED RED CHILI PEPPER",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Seasoning"
    )


def test_coors_maps_to_alcoholic_beverage() -> None:
    assert (
        categorize_item(
            "COORS LIGHT 6 PK HQ",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:AlcoholicBeverage"
    )


def test_project_rule_key_maps_via_account_config(tmp_path: Path) -> None:
    classifier = tmp_path / "item_classifier.toml"
    classifier.write_text(
        """
[[rules]]
id = "custom_test_rule"
keywords = ["CUSTOM NOODLE BRAND"]
key = "grocery_staple"
priority = 20
exact_only = true
""".strip()
    )

    account_map = tmp_path / "item_category_accounts.toml"
    account_map.write_text(
        """
[accounts]
grocery_staple = "Expenses:Food:Grocery:Staple"
""".strip()
    )

    assert (
        categorize_item(
            "CUSTOM NOODLE BRAND",
            rule_layers=load_item_category_rule_layers(
                classifier_paths=(str(classifier),),
                account_paths=(str(account_map),),
            ),
        )
        == "Expenses:Food:Grocery:Staple"
    )
