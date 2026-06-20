//! Self-contained rule loading for the on-device pipeline.
//!
//! Mirrors the desktop runtime loaders (`runtime/item_category_rules.py`,
//! `runtime/merchant_rules.py`) but owns TOML parsing in Rust so the iOS binary
//! needs no Python. The default rule data is bundled from the canonical
//! `rules/*.toml` (single source of truth shared with the desktop build).

use std::collections::HashMap;

use serde::Deserialize;

use crate::receipt_categories::{build_rule_layers, BuildClassifierConfig, BuildRuleEntry};
use crate::receipt_parser::ParserRuleLayers;

const DEFAULT_ITEM_CLASSIFIER_TOML: &str =
    include_str!("../../../rules/default_item_classifier.toml");
const DEFAULT_MERCHANT_RULES_TOML: &str = include_str!("../../../rules/default_merchant_rules.toml");

/// Two-stage category-key -> beancount-account mapping. Ported verbatim from
/// `receipt/item_categories.py::DEFAULT_CATEGORY_ACCOUNTS`.
pub fn default_category_accounts() -> HashMap<String, String> {
    [
        ("grocery_dairy", "Expenses:Food:Grocery:Dairy"),
        ("grocery_meat", "Expenses:Food:Grocery:Meat"),
        ("grocery_seafood_fish", "Expenses:Food:Grocery:Seafood:Fish"),
        ("grocery_seafood_shrimp", "Expenses:Food:Grocery:Seafood:Shrimp"),
        ("grocery_seafood", "Expenses:Food:Grocery:Seafood"),
        ("grocery_fruit", "Expenses:Food:Grocery:Fruit"),
        ("grocery_vegetable", "Expenses:Food:Grocery:Vegetable"),
        ("grocery_vegetable_canned", "Expenses:Food:Grocery:Vegetable:Canned"),
        ("grocery_frozen_dumpling", "Expenses:Food:Grocery:Frozen:Dumpling"),
        ("grocery_frozen_icecream", "Expenses:Food:Grocery:Frozen:IceCream"),
        ("grocery_frozen", "Expenses:Food:Grocery:Frozen"),
        ("grocery_prepared_meal", "Expenses:Food:Grocery:PreparedMeal"),
        ("grocery_bakery", "Expenses:Food:Grocery:Bakery"),
        ("grocery_staple", "Expenses:Food:Grocery:Staple"),
        ("grocery_seasoning", "Expenses:Food:Grocery:Seasoning"),
        ("grocery_snacks", "Expenses:Food:Grocery:Snacks"),
        ("grocery_snacks_mint", "Expenses:Food:Grocery:Snacks:Mint"),
        ("grocery_drink_cocacola", "Expenses:Food:Grocery:Drink:CocaCola"),
        ("grocery_drink_juice", "Expenses:Food:Grocery:Drink:Juice"),
        ("grocery_drink_coffee", "Expenses:Food:Grocery:Drink:Coffee"),
        ("grocery_drink", "Expenses:Food:Grocery:Drink"),
        ("alcoholic_beverage", "Expenses:Food:AlcoholicBeverage"),
        ("home_household_supply", "Expenses:Home:HouseholdSupply"),
        ("personal_care", "Expenses:PersonalCare"),
        ("personal_care_tooth", "Expenses:PersonalCare:Tooth"),
        ("pet", "Expenses:Pet"),
        ("pet_supply", "Expenses:Pet:Supply"),
        ("restaurant_gift_card", "Expenses:Food:Restaurant:GiftCard"),
        ("health_pharmacy", "Expenses:Health:Pharmacy"),
        ("shopping_clothing", "Expenses:Shopping:Clothing"),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v.to_string()))
    .collect()
}

/// Accepts a TOML value that is either a single string or a list of strings,
/// mirroring `python_receipt_categories.rs::string_or_list` (trim, drop empties).
#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum StringOrList {
    One(String),
    Many(Vec<String>),
}

impl StringOrList {
    fn into_trimmed(self) -> Vec<String> {
        match self {
            StringOrList::One(text) => {
                let cleaned = text.trim();
                if cleaned.is_empty() {
                    Vec::new()
                } else {
                    vec![cleaned.to_string()]
                }
            }
            StringOrList::Many(values) => values
                .into_iter()
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty())
                .collect(),
        }
    }
}

impl Default for StringOrList {
    fn default() -> Self {
        StringOrList::Many(Vec::new())
    }
}

/// Lowercase + dedupe tags, preserving first-seen order. Mirrors
/// `python_receipt_categories.rs::normalize_tags`.
fn normalize_tags(values: Vec<String>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut normalized = Vec::new();
    for value in values {
        let cleaned = value.trim().to_ascii_lowercase();
        if cleaned.is_empty() || !seen.insert(cleaned.clone()) {
            continue;
        }
        normalized.push(cleaned);
    }
    normalized
}

#[derive(Debug, Deserialize)]
struct RuleToml {
    #[serde(default)]
    keywords: StringOrList,
    #[serde(default)]
    key: Option<String>,
    #[serde(default)]
    category: Option<String>,
    #[serde(default)]
    tags: StringOrList,
    #[serde(default)]
    priority: i32,
    #[serde(default)]
    exact_only: bool,
}

#[derive(Debug, Default, Deserialize)]
struct ClassifierToml {
    #[serde(default)]
    exact_only_keywords: StringOrList,
    #[serde(default)]
    rules: Vec<RuleToml>,
}

fn to_build_config(parsed: ClassifierToml) -> BuildClassifierConfig {
    BuildClassifierConfig {
        exact_only_keywords: parsed.exact_only_keywords.into_trimmed(),
        rules: parsed
            .rules
            .into_iter()
            .map(|rule| {
                // `key` wins over `category`, then trim and treat empty as absent
                // (matches the PyBuildRuleEntry extraction order).
                let target = rule
                    .key
                    .or(rule.category)
                    .map(|value| value.trim().to_string())
                    .filter(|value| !value.is_empty());
                BuildRuleEntry {
                    keywords: rule.keywords.into_trimmed(),
                    target,
                    tags: normalize_tags(rule.tags.into_trimmed()),
                    priority: rule.priority,
                    exact_only: rule.exact_only,
                }
            })
            .collect(),
    }
}

fn parse_classifier(toml_text: &str) -> ClassifierToml {
    toml::from_str(toml_text).expect("bundled default_item_classifier.toml is valid")
}

/// Build the default item-category rule layers from the bundled classifier TOML
/// + the default account mapping (no project-local overrides — the iOS case).
pub fn default_parser_rule_layers() -> ParserRuleLayers {
    let config = to_build_config(parse_classifier(DEFAULT_ITEM_CLASSIFIER_TOML));
    let category_rules = build_rule_layers(default_category_accounts(), vec![config], vec![]);
    let account_mapping = category_rules
        .account_mapping
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    ParserRuleLayers {
        category_rules,
        account_mapping,
    }
}

#[derive(Debug, Deserialize)]
struct MerchantRuleToml {
    #[serde(default)]
    keywords: Vec<String>,
}

#[derive(Debug, Default, Deserialize)]
struct MerchantRulesToml {
    #[serde(default)]
    rules: Vec<MerchantRuleToml>,
}

/// Flatten merchant keywords from the bundled default merchant rules, preserving
/// file order. Mirrors `runtime/merchant_rules.py::load_known_merchant_keywords`
/// for the default-only (no project override) case.
pub fn default_known_merchants() -> Vec<String> {
    let parsed: MerchantRulesToml =
        toml::from_str(DEFAULT_MERCHANT_RULES_TOML).expect("bundled default_merchant_rules.toml is valid");
    parsed
        .rules
        .into_iter()
        .flat_map(|rule| rule.keywords)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_layers_load_and_resolve_known_categories() {
        let layers = default_parser_rule_layers();
        // Account mapping must include the ported defaults.
        assert_eq!(
            layers.account_mapping.iter().find(|(k, _)| k == "grocery_dairy").map(|(_, v)| v.as_str()),
            Some("Expenses:Food:Grocery:Dairy")
        );
        // Rules parsed from the bundled classifier TOML are non-empty.
        assert!(!layers.category_rules.rules.is_empty());
    }

    #[test]
    fn default_known_merchants_include_bundled_keywords() {
        let merchants = default_known_merchants();
        assert!(merchants.iter().any(|m| m == "COSTCO"));
    }
}
