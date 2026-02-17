"""Item categorization rules for receipt line items.

This module maps receipt item descriptions to expense categories.
Uses fuzzy matching (n-gram similarity) to handle OCR errors.
When multiple categories match, uses weighted scoring to pick the best one.

To add new rules:
1. Find the appropriate section below
2. Add keywords to an existing tuple, or add a new rule
3. Keywords are case-insensitive and matched with fuzzy tolerance
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

# Fuzzy matching thresholds (0.0 to 1.0)
# Higher = stricter matching, lower = more tolerant of OCR errors
# Short keywords use char frequency (allows 1 char error in 4-char word)
# Longer keywords use bigrams (more order-sensitive)
FUZZY_THRESHOLD_SHORT = 0.75  # For keywords <= 4 chars (3/4 chars must match)
FUZZY_THRESHOLD_MEDIUM = 0.80  # For keywords 5-6 chars (4/5 bigrams)
FUZZY_THRESHOLD_LONG = 0.70  # For keywords >= 7 chars

# Score bonus for exact matches (ensures exact beats fuzzy even at later positions)
EXACT_MATCH_BONUS = 1000
PRIORITY_SCORE_MULTIPLIER = 10000

# Keywords that should only match exactly (avoid fuzzy false positives)
# Built-in lists are intentionally kept empty; defaults now live in
# vendor/beanbeaver/receipt/rules/default_item_classifier.toml.
EXACT_ONLY_KEYWORDS: set[str] = set()

# Built-in rules are intentionally empty; see default_item_classifier.toml.
ITEM_RULES: list[tuple[tuple[str, ...], str]] = []
COSTCO_RULES: list[tuple[tuple[str, ...], str]] = []

# Two-stage category key -> beancount account mapping.
# Keys are optional for backward compatibility; existing rules may still return
# full beancount account strings directly.
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
}


RuleEntry = tuple[tuple[str, ...], str, int]


@dataclass(frozen=True)
class ItemCategoryRuleLayers:
    """In-memory categorization rules and account mapping."""

    rules: tuple[RuleEntry, ...]
    exact_only_keywords: frozenset[str]
    account_mapping: Mapping[str, str]


def _normalize_keywords(raw: Any) -> tuple[str, ...]:
    """Normalize keywords value from TOML into a non-empty tuple."""
    if isinstance(raw, str):
        value = raw.strip()
        return (value,) if value else tuple()
    if isinstance(raw, list):
        values = [str(v).strip() for v in raw if str(v).strip()]
        return tuple(values)
    return tuple()


def build_item_category_rule_layers(
    classifier_configs: Sequence[Mapping[str, Any]] | None = None,
    account_configs: Sequence[Mapping[str, Any]] | None = None,
) -> ItemCategoryRuleLayers:
    """Build merged rules/exact-only set/account mapping from in-memory configs."""
    # Built-in rules remain priority 0 and preserve existing behavior.
    rules: list[RuleEntry] = []
    for keywords, target in ITEM_RULES + COSTCO_RULES:
        keyword_tuple = (keywords,) if isinstance(keywords, str) else tuple(keywords)
        rules.append((keyword_tuple, target, 0))

    exact_only = set(EXACT_ONLY_KEYWORDS)
    classifier_configs = classifier_configs or ()
    for idx, config in enumerate(classifier_configs, start=1):
        layer_priority = idx * 100
        for raw_kw in config.get("exact_only_keywords", []):
            kw = str(raw_kw).strip()
            if kw:
                exact_only.add(kw)

        for rule in config.get("rules", []):
            if not isinstance(rule, Mapping):
                continue

            keywords = _normalize_keywords(rule.get("keywords"))
            if not keywords:
                continue

            target = str(rule.get("key") or rule.get("category") or "").strip()
            if not target:
                continue

            priority = int(rule.get("priority", 0)) + layer_priority
            rules.append((keywords, target, priority))

            if bool(rule.get("exact_only", False)):
                exact_only.update(keywords)

    account_mapping = dict(DEFAULT_CATEGORY_ACCOUNTS)
    for config in account_configs or ():
        accounts = config.get("accounts", {})
        if not isinstance(accounts, Mapping):
            continue
        for key, value in accounts.items():
            key_str = str(key).strip()
            value_str = str(value).strip()
            if key_str and value_str:
                account_mapping[key_str] = value_str

    return ItemCategoryRuleLayers(
        rules=tuple(rules),
        exact_only_keywords=frozenset(exact_only),
        account_mapping=account_mapping,
    )


@lru_cache(maxsize=1)
def _get_default_rule_layers() -> ItemCategoryRuleLayers:
    """Built-in-only default rules (no file I/O, no runtime deps)."""
    return build_item_category_rule_layers()


def _char_similarity(s1: str, s2: str) -> float:
    """Calculate character frequency similarity between two strings.

    Used for short keywords (<=5 chars) where bigrams don't work well.
    Returns ratio of common characters to keyword length.
    """
    c1, c2 = Counter(s1), Counter(s2)
    common = sum((c1 & c2).values())
    return common / len(s1) if s1 else 0.0


def _bigram_similarity(s1: str, s2: str) -> float:
    """Calculate bigram (2-gram) similarity between two strings.

    Used for longer keywords where bigrams capture structure better.
    Returns ratio of common bigrams to keyword bigrams.
    """
    if len(s1) < 2:
        return 1.0 if s1 in s2 else 0.0

    bigrams1 = {s1[i : i + 2] for i in range(len(s1) - 1)}
    bigrams2 = {s2[i : i + 2] for i in range(len(s2) - 1)}

    if not bigrams1:
        return 0.0

    return len(bigrams1 & bigrams2) / len(bigrams1)


def _get_threshold(kw_len: int) -> float:
    """Get appropriate fuzzy matching threshold based on keyword length."""
    if kw_len <= 4:
        return FUZZY_THRESHOLD_SHORT
    elif kw_len <= 6:
        return FUZZY_THRESHOLD_MEDIUM
    else:
        return FUZZY_THRESHOLD_LONG


def _fuzzy_contains(keyword: str, description: str, threshold: float | None = None) -> tuple[bool, int, bool]:
    """Check if keyword appears fuzzily in description using n-gram similarity.

    Handles OCR errors like: EGOS->EGGS, M1LK->MILK, CHIC KEN->CHICKEN

    Args:
        keyword: The keyword to search for
        description: The item description to search in
        threshold: Minimum similarity score (0.0 to 1.0), auto-determined if None

    Returns:
        Tuple of (matched: bool, position: int, is_exact: bool)
        Position is -1 if no match.
    """
    # Normalize: uppercase, remove spaces
    desc_raw = description.upper()
    kw_raw = keyword.upper().strip()

    # Very short keywords (1-3 chars): exact whole-word match only
    # This avoids false positives like TEA matching in STEAK.
    kw_len_raw = len(kw_raw.replace(" ", ""))
    if kw_len_raw <= 3:
        match = re.search(r"\b" + re.escape(kw_raw) + r"\b", desc_raw)
        if match:
            return True, match.start(), True
        return False, -1, False

    desc = desc_raw.replace(" ", "")
    kw = kw_raw.replace(" ", "")

    # Exact containment (fast path)
    exact_pos = desc.find(kw)
    if exact_pos != -1:
        return True, exact_pos, True

    kw_len = len(kw)

    # Determine threshold based on keyword length
    if threshold is None:
        threshold = _get_threshold(kw_len)

    # If threshold is 1.0, we only accept exact matches (already checked above)
    if threshold >= 1.0:
        return False, -1, False

    # Slide window over description, looking for fuzzy match
    window_size = kw_len + 1  # Allow 1 char buffer for insertions
    best_similarity = 0.0
    best_position = -1

    for start in range(len(desc) - kw_len + 2):
        window = desc[start : start + window_size]

        # Use bigram similarity for all keywords (order-sensitive)
        # Character frequency is too permissive (ignores order completely)
        similarity = _bigram_similarity(kw, window)

        if similarity > best_similarity:
            best_similarity = similarity
            best_position = start

    if best_similarity >= threshold:
        return True, best_position, False

    return False, -1, False


def _find_all_matches(
    description: str,
    rules: Sequence[RuleEntry],
    exact_only_keywords: set[str] | frozenset[str],
) -> list[tuple[int, str, str, int]]:
    """Find all matching categories for a description.

    Returns list of (score, category, matched_keyword, position) tuples.
    Score = keyword_length * 10 + position + exact_match_bonus
    Prefers: exact matches > fuzzy matches, longer keywords > shorter, later position > earlier
    """
    matches = []

    for keywords, category, priority in rules:
        for kw in keywords:
            threshold = 1.0 if kw in exact_only_keywords else None
            matched, position, is_exact = _fuzzy_contains(kw, description, threshold=threshold)
            if matched:
                # Score: longer keyword = more specific, later position = likely the main item
                # Exact matches get a large bonus to ensure they beat fuzzy matches
                kw_len = len(kw.replace(" ", ""))
                score = kw_len * 10 + position + priority * PRIORITY_SCORE_MULTIPLIER
                if is_exact:
                    score += EXACT_MATCH_BONUS
                matches.append((score, category, kw, position))
                break  # Only need one keyword match per category

    return matches


def classify_item_key(
    description: str,
    default: str | None = None,
    rule_layers: ItemCategoryRuleLayers | None = None,
) -> str | None:
    """Classify an item to an internal category key or direct account target."""
    layers = rule_layers or _get_default_rule_layers()

    matches = _find_all_matches(description, layers.rules, layers.exact_only_keywords)

    if not matches:
        return default

    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def _resolve_account_target(
    target: str | None,
    account_mapping: Mapping[str, str],
    default: str | None = None,
) -> str | None:
    """Resolve an internal key to a concrete beancount account."""
    if target is None:
        return default
    if target.startswith("Expenses:"):
        return target
    return account_mapping.get(target, default)


def categorize_item(
    description: str,
    default: str | None = None,
    rule_layers: ItemCategoryRuleLayers | None = None,
) -> str | None:
    """
    Return expense category for an item description.

    Uses fuzzy matching to handle OCR errors and weighted scoring when
    multiple categories match (prefers longer keywords appearing later
    in the description).

    Args:
        description: Item description from receipt (e.g., "LARGE EGGS 18CT")
        default: Category to return if no rule matches
        rule_layers: Preloaded in-memory rules (typically from runtime loader).

    Returns:
        Category string (e.g., "Expenses:Food:Grocery:Dairy") or default if no match

    When rule_layers is omitted, only built-in rules apply.
    """
    layers = rule_layers or _get_default_rule_layers()

    matches = _find_all_matches(description, layers.rules, layers.exact_only_keywords)
    if not matches:
        return default

    matches.sort(key=lambda x: x[0], reverse=True)
    return _resolve_account_target(matches[0][1], layers.account_mapping, default=default)


def categorize_item_debug(
    description: str,
    rule_layers: ItemCategoryRuleLayers | None = None,
) -> list[tuple[str, str, float]]:
    """Debug version that returns all matches with scores.

    Useful for understanding why a particular category was chosen.

    Returns:
        List of (category, matched_keyword, score) tuples, sorted by score descending
    """
    layers = rule_layers or _get_default_rule_layers()

    matches = _find_all_matches(description, layers.rules, layers.exact_only_keywords)
    matches.sort(key=lambda x: x[0], reverse=True)
    return [
        (_resolve_account_target(cat, layers.account_mapping, default=cat) or cat, kw, score)
        for score, cat, kw, _ in matches
    ]
