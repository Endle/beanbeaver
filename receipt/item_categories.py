"""Item categorization rules for receipt line items.

This module classifies receipt item descriptions into semantic categories/tags,
then separately maps semantic category keys to Beancount accounts when needed.
Uses fuzzy matching (n-gram similarity) to handle OCR errors.

To add new rules:
1. Find the appropriate section below
2. Add keywords to an existing tuple, or add a new rule
3. Keywords are case-insensitive and matched with fuzzy tolerance
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Fuzzy matching thresholds (0.0 to 1.0)
# Higher = stricter matching, lower = more tolerant of OCR errors
# Short keywords use char frequency (allows 1 char error in 4-char word)
# Longer keywords use bigrams (more order-sensitive)
FUZZY_THRESHOLD_SHORT = 0.75  # For keywords <= 4 chars (3/4 chars must match)
FUZZY_THRESHOLD_MEDIUM = 0.80  # For keywords 5-6 chars (4/5 bigrams)
FUZZY_THRESHOLD_LONG = 0.70  # For keywords >= 7 chars

# Keywords that should only match exactly (avoid fuzzy false positives)
# Built-in lists are intentionally kept empty; defaults now live in
# vendor/beanbeaver/rules/default_item_classifier.toml.
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
    "health_pharmacy": "Expenses:Health:Pharmacy",
    "shopping_clothing": "Expenses:Shopping:Clothing",
}


OCR_CONFUSABLE_TRANS_TABLE = str.maketrans("0D", "OO")


@dataclass(frozen=True)
class RuleEntry:
    """One semantic classification rule."""

    keywords: tuple[str, ...]
    category: str | None
    tags: tuple[str, ...]
    priority: int


@dataclass(frozen=True)
class RuleMatch:
    """One successful keyword match against a rule."""

    category: str | None
    tags: tuple[str, ...]
    matched_keyword: str
    priority: int
    keyword_length: int
    is_exact: bool
    rule_index: int


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


def _normalize_tags(raw: Any) -> tuple[str, ...]:
    """Normalize tags value from TOML into a deduplicated tuple."""
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = [str(v) for v in raw]
    else:
        return tuple()

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = str(value).strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return tuple(normalized)


def _invert_account_mapping(account_mapping: Mapping[str, str]) -> dict[str, str]:
    """Return Beancount account -> semantic category key mapping."""
    inverted: dict[str, str] = {}
    for key, account in account_mapping.items():
        inverted.setdefault(account, key)
    return inverted


def _normalize_rule_target(
    target: str | None,
    *,
    account_mapping: Mapping[str, str],
) -> str | None:
    """Normalize a rule target to a semantic category key when possible."""
    if not target:
        return None

    cleaned = target.strip()
    if not cleaned:
        return None

    if cleaned.startswith("Expenses:"):
        return _invert_account_mapping(account_mapping).get(cleaned, cleaned)
    return cleaned


def build_item_category_rule_layers(
    classifier_configs: Sequence[Mapping[str, Any]] | None = None,
    account_configs: Sequence[Mapping[str, Any]] | None = None,
) -> ItemCategoryRuleLayers:
    """Build merged rules/exact-only set/account mapping from in-memory configs."""
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

    # Built-in rules remain priority 0 and preserve existing behavior.
    rules: list[RuleEntry] = []
    for keywords, target in ITEM_RULES + COSTCO_RULES:
        rules.append(
            RuleEntry(
                keywords=tuple(keywords),
                category=_normalize_rule_target(target, account_mapping=account_mapping),
                tags=tuple(),
                priority=0,
            )
        )

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

            target_raw = str(rule.get("key") or rule.get("category") or "").strip()
            category = _normalize_rule_target(target_raw, account_mapping=account_mapping)
            tags = _normalize_tags(rule.get("tags"))
            if category is None and not tags:
                continue

            priority = int(rule.get("priority", 0)) + layer_priority
            rules.append(
                RuleEntry(
                    keywords=keywords,
                    category=category,
                    tags=tags,
                    priority=priority,
                )
            )

            if bool(rule.get("exact_only", False)):
                exact_only.update(keywords)

    return ItemCategoryRuleLayers(
        rules=tuple(rules),
        exact_only_keywords=frozenset(exact_only),
        account_mapping=account_mapping,
    )


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


def _normalize_ocr_confusables(text: str) -> str:
    """Normalize common OCR-confused glyphs used in item matching."""
    return text.translate(OCR_CONFUSABLE_TRANS_TABLE)


def _contains_with_single_char_noise(keyword: str, description: str) -> tuple[bool, int]:
    """Match multi-word keyword allowing one single-char OCR token between words."""
    kw_tokens = [tok for tok in keyword.upper().split() if tok]
    if len(kw_tokens) < 2:
        return False, -1

    normalized_desc = re.sub(r"[^A-Z0-9]+", " ", description.upper()).strip()
    if not normalized_desc:
        return False, -1

    pattern = r"\b" + re.escape(kw_tokens[0]) + r"\b"
    for token in kw_tokens[1:]:
        pattern += r"(?:\s+[A-Z0-9]\b)?\s+\b" + re.escape(token) + r"\b"

    match = re.search(pattern, normalized_desc)
    if not match:
        return False, -1
    return True, match.start()


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
    desc_conf_raw = _normalize_ocr_confusables(desc_raw)
    kw_conf_raw = _normalize_ocr_confusables(kw_raw)
    exact_only = threshold is not None and threshold >= 1.0

    # Very short keywords (1-3 chars): exact whole-word match only
    # This avoids false positives like TEA matching in STEAK.
    kw_len_raw = len(kw_raw.replace(" ", ""))
    if kw_len_raw <= 3:
        match = re.search(r"\b" + re.escape(kw_raw) + r"\b", desc_raw)
        if match:
            return True, match.start(), True
        # Optional OCR-tolerant exact for confusable glyphs (e.g., D/O/0).
        if not exact_only:
            for token_match in re.finditer(r"[A-Z0-9]+", desc_raw):
                if _normalize_ocr_confusables(token_match.group(0)) == kw_conf_raw:
                    return True, token_match.start(), True
        return False, -1, False

    desc = desc_raw.replace(" ", "")
    kw = kw_raw.replace(" ", "")
    desc_conf = desc_conf_raw.replace(" ", "")
    kw_conf = kw_conf_raw.replace(" ", "")

    # Exact containment (fast path)
    exact_pos = desc.find(kw)
    if exact_pos != -1:
        return True, exact_pos, True
    if not exact_only:
        exact_pos_conf = desc_conf.find(kw_conf)
        if exact_pos_conf != -1:
            return True, exact_pos_conf, True

    # Treat "CHOCOLATE E MILK"-style OCR splits as exact phrase matches.
    noisy_phrase_match, noisy_phrase_pos = _contains_with_single_char_noise(kw_raw, desc_raw)
    if noisy_phrase_match:
        return True, noisy_phrase_pos, True
    if not exact_only:
        noisy_phrase_match_conf, noisy_phrase_pos_conf = _contains_with_single_char_noise(kw_conf_raw, desc_conf_raw)
        if noisy_phrase_match_conf:
            return True, noisy_phrase_pos_conf, True

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

    for start in range(len(desc_conf) - kw_len + 2):
        window = desc_conf[start : start + window_size]

        # Use bigram similarity for all keywords (order-sensitive)
        # Character frequency is too permissive (ignores order completely)
        similarity = _bigram_similarity(kw_conf, window)

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
) -> list[RuleMatch]:
    """Find all matching rules for a description."""
    matches: list[RuleMatch] = []

    for rule_index, rule in enumerate(rules):
        for kw in rule.keywords:
            threshold = 1.0 if kw in exact_only_keywords else None
            matched, _, is_exact = _fuzzy_contains(kw, description, threshold=threshold)
            if matched:
                kw_len = len(kw.replace(" ", ""))
                matches.append(
                    RuleMatch(
                        category=rule.category,
                        tags=rule.tags,
                        matched_keyword=kw,
                        priority=rule.priority,
                        keyword_length=kw_len,
                        is_exact=is_exact,
                        rule_index=rule_index,
                    )
                )
                break  # Only need one keyword match per rule

    return matches


def _match_sort_key(match: RuleMatch) -> tuple[int, int, int, int]:
    """Return deterministic category ranking key."""
    return (
        match.priority,
        1 if match.is_exact else 0,
        match.keyword_length,
        -match.rule_index,
    )


def classify_item_key(
    description: str,
    rule_layers: ItemCategoryRuleLayers,
    default: str | None = None,
) -> str | None:
    """Classify an item to a semantic category key."""
    matches = _find_all_matches(description, rule_layers.rules, rule_layers.exact_only_keywords)
    category_matches = [match for match in matches if match.category]

    if not category_matches:
        return default

    return max(category_matches, key=_match_sort_key).category


def classify_item_tags(
    description: str,
    rule_layers: ItemCategoryRuleLayers,
) -> list[str]:
    """Return additive semantic tags for one item description."""
    matches = _find_all_matches(description, rule_layers.rules, rule_layers.exact_only_keywords)
    tags: list[str] = []
    seen: set[str] = set()
    for match in matches:
        for tag in match.tags:
            if tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)
    return tags


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


def account_for_category_key(
    category: str | None,
    account_mapping: Mapping[str, str] | None = None,
    *,
    default: str | None = None,
) -> str | None:
    """Resolve one semantic category key to a Beancount account."""
    return _resolve_account_target(category, account_mapping or DEFAULT_CATEGORY_ACCOUNTS, default=default)


def categorize_item(
    description: str,
    default: str | None = None,
    *,
    rule_layers: ItemCategoryRuleLayers,
) -> str | None:
    """
    Return Beancount expense account for an item description.

    Uses fuzzy matching to handle OCR errors and weighted scoring when
    multiple categories match (prefers longer keywords appearing later
    in the description).

    Args:
        description: Item description from receipt (e.g., "LARGE EGGS 18CT")
        default: Category to return if no rule matches
        rule_layers: Preloaded in-memory rules (required).

    Returns:
        Account string (e.g., "Expenses:Food:Grocery:Dairy") or default if no match

    """
    category = classify_item_key(description, rule_layers)
    return _resolve_account_target(category, rule_layers.account_mapping, default=default)


def categorize_item_debug(
    description: str,
    rule_layers: ItemCategoryRuleLayers,
) -> list[tuple[str, str, float]]:
    """Debug version that returns all matches with scores.

    Useful for understanding why a particular category was chosen.

    Returns:
        List of (category, matched_keyword, score) tuples, sorted by score descending
    """
    matches = _find_all_matches(description, rule_layers.rules, rule_layers.exact_only_keywords)
    matches.sort(key=_match_sort_key, reverse=True)
    return [
        (
            _resolve_account_target(match.category, rule_layers.account_mapping, default=match.category)
            or (match.category or ""),
            match.matched_keyword,
            float(match.priority * 10000 + (1000 if match.is_exact else 0) + match.keyword_length),
        )
        for match in matches
    ]
