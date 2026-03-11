"""Tests for receipt item category matching."""

from pathlib import Path

import pytest
from beanbeaver.receipt.item_categories import (
    account_for_category_key,
    categorize_item,
    classify_item_semantic,
    list_item_categories,
)
from beanbeaver.runtime.item_category_rules import load_item_category_rule_layers, load_receipt_structuring_rule_layers


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


def test_semantic_classification_includes_explicit_tags() -> None:
    classification = classify_item_semantic(
        "347937 CHICKEN",
        load_receipt_structuring_rule_layers(),
    )

    assert classification == {
        "category": "grocery_meat",
        "tags": ["grocery", "meat", "chicken"],
        "confidence": 1.0,
        "source": "rule_engine",
    }


def test_fine_filt_includes_milk_tag() -> None:
    classification = classify_item_semantic(
        "435259 FINE-FILT",
        load_receipt_structuring_rule_layers(),
    )

    assert classification == {
        "category": "grocery_dairy",
        "tags": ["grocery", "dairy", "milk"],
        "confidence": 1.0,
        "source": "rule_engine",
    }


def test_lcbo_card_emits_alcohol_and_giftcard_tags_without_category() -> None:
    classification = classify_item_semantic(
        "810 LCBO CARD",
        load_receipt_structuring_rule_layers(),
    )

    assert classification == {
        "category": None,
        "tags": ["alcohol", "gift_card"],
        "confidence": 1.0,
        "source": "rule_engine",
    }


def test_monster_includes_energy_drink_tag() -> None:
    classification = classify_item_semantic(
        "2773717 MONSTER VRTY",
        load_receipt_structuring_rule_layers(),
    )

    assert classification == {
        "category": "grocery_drink",
        "tags": ["grocery", "drink", "energy_drink"],
        "confidence": 1.0,
        "source": "rule_engine",
    }


def test_step2_rules_ignore_project_classifier_override() -> None:
    classification = classify_item_semantic(
        "MARUTAI",
        load_receipt_structuring_rule_layers(),
    )

    assert classification is None


def test_sonicare_maps_to_personal_care_tooth() -> None:
    assert (
        categorize_item(
            "SONICARE TOOTHBRUSH HEADS",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:PersonalCare:Tooth"
    )


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("HLY - Fish Cracker Seawee", "Expenses:Food:Grocery:Snacks"),
        ("TY - Lemon Tea", "Expenses:Food:Grocery:Drink"),
        ("LHL - Malatang Slightly S", "Expenses:Food:Grocery:Staple"),
        ("Pork Lard", "Expenses:Food:Grocery:Seasoning"),
        ("BQ - Frozen Raw Peeled Un", "Expenses:Food:Grocery:Seafood:Shrimp"),
        ("BAKERY", "Expenses:Food:Grocery:Bakery"),
        ("Hot Food", "Expenses:Food:Grocery:PreparedMeal"),
    ],
)
def test_public_default_foodmart_overrides(description: str, expected: str) -> None:
    assert (
        categorize_item(
            description,
            rule_layers=load_item_category_rule_layers(),
        )
        == expected
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


def test_list_item_categories_returns_sorted_key_account_pairs(tmp_path: Path) -> None:
    classifier = tmp_path / "item_classifier.toml"
    classifier.write_text(
        """
[[rules]]
id = "custom_direct_account"
keywords = ["CUSTOM DIRECT ACCOUNT"]
category = "Expenses:Project:Custom"
""".strip()
    )

    account_map = tmp_path / "item_category_accounts.toml"
    account_map.write_text(
        """
[accounts]
zzz_custom = "Expenses:Project:Zzz"
""".strip()
    )

    categories = list_item_categories(
        load_item_category_rule_layers(
            classifier_paths=(str(classifier),),
            account_paths=(str(account_map),),
        )
    )

    assert categories == sorted(categories, key=lambda entry: entry[0])
    assert ("Expenses:Project:Custom", "Expenses:Project:Custom") in categories
    assert ("zzz_custom", "Expenses:Project:Zzz") in categories


def test_legacy_direct_icecream_account_alias_maps_to_frozen_icecream() -> None:
    assert account_for_category_key("Expenses:Food:Grocery:IceCream") == "Expenses:Food:Grocery:Frozen:IceCream"


def test_pork_large_intestine_prefers_meat_over_lard_false_positive() -> None:
    assert (
        categorize_item(
            "Pork Large Intestine",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Meat"
    )


def test_fruit_ft_header_maps_to_fruit() -> None:
    assert (
        categorize_item(
            "&& Fruit (FT)",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Fruit"
    )


def test_wing_hing_sweet_soy_bever_prefix_maps_to_drink() -> None:
    assert (
        categorize_item(
            "Wing Hing Sweet Soy Bever",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Drink"
    )


def test_champ_short_maps_to_clothing_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "1944033 CHAMP SHORT",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Shopping:Clothing"
    )


def test_ks_bags_60_maps_to_household_supply_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "295619 KS BAGS 60",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Home:HouseholdSupply"
    )


def test_rainforest_maps_to_coffee_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "108934 RAINFOREST",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Drink:Coffee"
    )


def test_swiffer_dust_maps_to_household_supply_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "1218587 SWIFFER DUST",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Home:HouseholdSupply"
    )


def test_tide_maps_to_household_supply_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "3458556 TIDE CQLDWTR",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Home:HouseholdSupply"
    )


def test_skechers_maps_to_clothing_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "2946010 SKECHERSGLID",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Shopping:Clothing"
    )


def test_cascade_plus_maps_to_household_supply_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "1727590 CASCADE PLUS",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Home:HouseholdSupply"
    )


def test_baking_soda_prefers_household_supply_over_cocacola_soda_keyword() -> None:
    assert (
        categorize_item(
            "1185 BAKING SODA",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Home:HouseholdSupply"
    )


def test_glide_adv_maps_to_tooth_care_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "1457015 GLIDE ADV",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:PersonalCare:Tooth"
    )


def test_white_rabbit_maps_to_snacks_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "1968518 WHITE RABBIT",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Snacks"
    )


def test_marc_anthony_maps_to_personal_care_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "443404 MARC ANTHONY",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:PersonalCare"
    )


def test_toliet_2pk_maps_to_household_supply_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "1796144 TOLIET 2PK",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Home:HouseholdSupply"
    )


def test_fo_tank_s_maps_to_clothing_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "3966510 FO TANK S",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Shopping:Clothing"
    )


def test_lavazza_1kg_maps_to_coffee_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "599010 LAVAZZA 1KG",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Drink:Coffee"
    )


def test_togo_van_2kg_maps_to_dairy_with_low_priority_public_rule() -> None:
    assert (
        categorize_item(
            "1355285 TOGO VAN 2KG",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Dairy"
    )


def test_pineapple_bun_prefers_bakery_with_high_priority_bun_rule() -> None:
    assert (
        categorize_item(
            "Red Bean Pinapple Bun",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Bakery"
    )


def test_sandwich_biscuits_matcha_maps_to_snacks_with_high_priority_public_rule() -> None:
    assert (
        categorize_item(
            "*Sandwich Biscuits(Matcha)",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Snacks"
    )


def test_white_pepper_powder_maps_to_seasoning_with_high_priority_public_rule() -> None:
    assert (
        categorize_item(
            "D.M.D White Pepper Powder",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Seasoning"
    )


def test_yuan_qi_sen_lin_iced_tea_maps_to_drink_with_high_priority_public_rule() -> None:
    assert (
        categorize_item(
            "*Yuan Qi Sen Lin Iced Tea",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Drink"
    )


def test_tropicana_daily_c_tea_maps_to_drink_with_high_priority_public_rule() -> None:
    assert (
        categorize_item(
            "'Tropicana Daily C Tea Dr ×1",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Drink"
    )


def test_orion_double_choco_pie_maps_to_snacks_with_high_priority_public_rule() -> None:
    assert (
        categorize_item(
            "*Or:ion Double Choco Pie 12",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Snacks"
    )


def test_yyh_chillies_maps_to_seasoning_with_low_risk_public_rule() -> None:
    assert (
        categorize_item(
            "YYH Chillies 80g",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Seasoning"
    )


def test_la_pian_maps_to_snacks_with_low_risk_public_rule() -> None:
    assert (
        categorize_item(
            "La Pian (Spicy Gluten Sli",
            rule_layers=load_item_category_rule_layers(),
        )
        == "Expenses:Food:Grocery:Snacks"
    )
