"""Tests for receipt item category matching."""

from pathlib import Path

import pytest
from beanbeaver.receipt.item_categories import categorize_item
from beanbeaver.runtime.item_category_rules import load_item_category_rule_layers


@pytest.mark.parametrize(
    "description",
    [
        "SAPORITO FOODS CORN OIL 2.84L",
        "FLOWER PERICARPIURN ZANTHOXYLI",
        "T&T SLICED RED CHILI PEPPER",
    ],
)
def test_seasoning_examples(description: str) -> None:
    assert (
        categorize_item(
            description,
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


def test_sonicare_maps_to_personal_care_tooth() -> None:
    assert (
        categorize_item(
            "SONICARE TOOTHBRUSH HEADS",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:PersonalCare:Tooth"
    )


def test_chocolate_milk_with_single_char_noise_maps_to_dairy() -> None:
    assert (
        categorize_item(
            "NEILSON JOYYA CHOCOLATE E MILK",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Dairy"
    )


@pytest.mark.parametrize(
    "description",
    [
        "LYSOL BATH P 059631882930",
        "LYS0L BATH P 059631882930",
        "LYSDL BATH P 059631882930",
    ],
)
def test_lysol_with_d_o_0_noise_maps_to_household_supply(description: str) -> None:
    assert (
        categorize_item(
            description,
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Home:HouseholdSupply"
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
