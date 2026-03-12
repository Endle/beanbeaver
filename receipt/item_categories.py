"""Item categorization rules for receipt line items."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ._rust import require_rust_matcher

# Built-in lists remain empty; defaults live in rules/default_item_classifier.toml.
EXACT_ONLY_KEYWORDS: set[str] = set()
ITEM_RULES: list[tuple[tuple[str, ...], str]] = []
COSTCO_RULES: list[tuple[tuple[str, ...], str]] = []

# Two-stage category key -> beancount account mapping.
DEFAULT_CATEGORY_ACCOUNTS: dict[str, str] = {
    "grocery_dairy": "Expenses:Food:Grocery:Dairy",
    "grocery_meat": "Expenses:Food:Grocery:Meat",
    "grocery_seafood_fish": "Expenses:Food:Grocery:Seafood:Fish",
    "grocery_seafood_shrimp": "Expenses:Food:Grocery:Seafood:Shrimp",
    "grocery_seafood": "Expenses:Food:Grocery:Seafood",
    "grocery_fruit": "Expenses:Food:Grocery:Fruit",
    "grocery_vegetable": "Expenses:Food:Grocery:Vegetable",
    "grocery_vegetable_canned": "Expenses:Food:Grocery:Vegetable:Canned",
    "grocery_frozen_dumpling": "Expenses:Food:Grocery:Frozen:Dumpling",
    "grocery_frozen_icecream": "Expenses:Food:Grocery:Frozen:IceCream",
    "grocery_frozen": "Expenses:Food:Grocery:Frozen",
    "grocery_prepared_meal": "Expenses:Food:Grocery:PreparedMeal",
    "grocery_bakery": "Expenses:Food:Grocery:Bakery",
    "grocery_staple": "Expenses:Food:Grocery:Staple",
    "grocery_seasoning": "Expenses:Food:Grocery:Seasoning",
    "grocery_snacks": "Expenses:Food:Grocery:Snacks",
    "grocery_snacks_mint": "Expenses:Food:Grocery:Snacks:Mint",
    "grocery_drink_cocacola": "Expenses:Food:Grocery:Drink:CocaCola",
    "grocery_drink_juice": "Expenses:Food:Grocery:Drink:Juice",
    "grocery_drink_coffee": "Expenses:Food:Grocery:Drink:Coffee",
    "grocery_drink": "Expenses:Food:Grocery:Drink",
    "alcoholic_beverage": "Expenses:Food:AlcoholicBeverage",
    "home_household_supply": "Expenses:Home:HouseholdSupply",
    "personal_care": "Expenses:PersonalCare",
    "personal_care_tooth": "Expenses:PersonalCare:Tooth",
    "pet": "Expenses:Pet",
    "pet_supply": "Expenses:Pet:Supply",
    "restaurant_gift_card": "Expenses:Food:Restaurant:GiftCard",
    "health_pharmacy": "Expenses:Health:Pharmacy",
    "shopping_clothing": "Expenses:Shopping:Clothing",
}


@dataclass(frozen=True)
class RuleEntry:
    """One semantic classification rule."""

    keywords: tuple[str, ...]
    category: str | None
    tags: tuple[str, ...]
    priority: int


@dataclass(frozen=True)
class ItemCategoryRuleLayers:
    """In-memory categorization rules and account mapping."""

    rules: tuple[RuleEntry, ...]
    exact_only_keywords: frozenset[str]
    account_mapping: Mapping[str, str]


def build_item_category_rule_layers(
    classifier_configs: Sequence[Mapping[str, Any]] | None = None,
    account_configs: Sequence[Mapping[str, Any]] | None = None,
) -> ItemCategoryRuleLayers:
    """Build merged rules/exact-only set/account mapping from in-memory configs."""
    built = require_rust_matcher().receipt_build_item_category_rule_layers(
        DEFAULT_CATEGORY_ACCOUNTS,
        list(classifier_configs or ()),
        list(account_configs or ()),
    )
    return ItemCategoryRuleLayers(
        rules=tuple(
            RuleEntry(
                keywords=tuple(keywords),
                category=category,
                tags=tuple(tags),
                priority=priority,
            )
            for keywords, category, tags, priority in built.get("rules", [])
        ),
        exact_only_keywords=frozenset(str(value) for value in built.get("exact_only_keywords", [])),
        account_mapping=dict(built.get("account_mapping") or {}),
    )


def classify_item_key(
    description: str,
    rule_layers: ItemCategoryRuleLayers,
    default: str | None = None,
) -> str | None:
    """Classify an item to a semantic category key."""
    return require_rust_matcher().receipt_classify_item_key(description, rule_layers, default)


def classify_item_tags(
    description: str,
    rule_layers: ItemCategoryRuleLayers,
) -> list[str]:
    """Return additive semantic tags for one item description."""
    return list(require_rust_matcher().receipt_classify_item_tags(description, rule_layers))


def list_item_categories(rule_layers: ItemCategoryRuleLayers) -> list[tuple[str, str]]:
    """Return deterministic category options as (semantic_key, beancount_account)."""
    return list(require_rust_matcher().receipt_list_item_categories(rule_layers))


def classify_item_semantic(
    description: str,
    rule_layers: ItemCategoryRuleLayers,
    *,
    default_category: str | None = None,
) -> dict[str, Any] | None:
    """Return semantic classification payload for one item description."""
    category = classify_item_key(description, rule_layers, default=default_category)
    tags = classify_item_tags(description, rule_layers)
    if category is None and not tags:
        return None
    return {
        "category": category,
        "tags": tags,
        "confidence": 1.0,
        "source": "rule_engine",
    }


def account_for_category_key(
    category: str | None,
    account_mapping: Mapping[str, str] | None = None,
    *,
    default: str | None = None,
) -> str | None:
    """Resolve one semantic category key to a Beancount account."""
    return require_rust_matcher().receipt_account_for_category_key(
        category,
        dict(account_mapping or DEFAULT_CATEGORY_ACCOUNTS),
        default,
    )


def categorize_item(
    description: str,
    default: str | None = None,
    *,
    rule_layers: ItemCategoryRuleLayers,
) -> str | None:
    """Return Beancount expense account for an item description."""
    category = classify_item_key(description, rule_layers)
    return account_for_category_key(category, rule_layers.account_mapping, default=default)


def categorize_item_debug(
    description: str,
    rule_layers: ItemCategoryRuleLayers,
) -> list[tuple[str, str, float]]:
    """Debug version that returns all matches with scores."""
    matches = require_rust_matcher().receipt_find_item_matches(description, rule_layers)
    return [
        (
            account_for_category_key(category, rule_layers.account_mapping, default=category) or (category or ""),
            matched_keyword,
            float(priority * 10000 + (1000 if is_exact else 0) + keyword_length),
        )
        for category, matched_keyword, priority, keyword_length, is_exact, _rule_index in matches
    ]
