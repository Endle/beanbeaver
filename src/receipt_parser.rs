use std::collections::HashSet;

use crate::receipt_categories;
use crate::receipt_fields;
use crate::receipt_parse_helpers;
use crate::receipt_spatial;
use crate::receipt_text;

#[derive(Clone, Debug)]
pub(crate) struct ParserRuleLayers {
    pub(crate) category_rules: receipt_categories::CategoryRuleLayers,
    pub(crate) account_mapping: Vec<(String, String)>,
}

#[derive(Clone, Debug)]
pub(crate) struct ParsedReceiptItem {
    pub(crate) description: String,
    pub(crate) price: String,
    pub(crate) quantity: i32,
    pub(crate) category: Option<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct ParsedReceiptWarning {
    pub(crate) message: String,
    pub(crate) after_item_index: Option<usize>,
}

#[derive(Clone, Debug)]
pub(crate) struct ParsedReceiptData {
    pub(crate) merchant: String,
    pub(crate) date: Option<(i32, u32, u32)>,
    pub(crate) date_is_placeholder: bool,
    pub(crate) total: String,
    pub(crate) items: Vec<ParsedReceiptItem>,
    pub(crate) tax: Option<String>,
    pub(crate) subtotal: Option<String>,
    pub(crate) raw_text: String,
    pub(crate) image_filename: String,
    pub(crate) warnings: Vec<ParsedReceiptWarning>,
}

fn cents_to_fixed(value: i64) -> String {
    let sign = if value < 0 { "-" } else { "" };
    let abs = value.abs();
    format!("{sign}{}.{:02}", abs / 100, abs % 100)
}

fn scaled_to_fixed(value: i64, scale: i64) -> String {
    let sign = if value < 0 { "-" } else { "" };
    let abs = value.abs();
    let whole = abs / scale;
    let frac = abs % scale;
    format!("{sign}{whole}.{:04}", frac)
}

fn legacy_account_alias(target: &str) -> Option<&'static str> {
    match target {
        "Expenses:Food:Grocery:Dumolings" => Some("Expenses:Food:Grocery:Frozen:Dumpling"),
        "Expenses:Food:Grocery:Dumplings" => Some("Expenses:Food:Grocery:Frozen:Dumpling"),
        "Expenses:Food:Grocery:Icecream" => Some("Expenses:Food:Grocery:Frozen:IceCream"),
        "Expenses:Food:Grocery:IceCream" => Some("Expenses:Food:Grocery:Frozen:IceCream"),
        _ => None,
    }
}

fn normalize_legacy_account_target(target: &str) -> String {
    legacy_account_alias(target).unwrap_or(target).to_string()
}

fn resolve_account_target(
    target: Option<&str>,
    rule_layers: &ParserRuleLayers,
    default: Option<&str>,
) -> Option<String> {
    match target {
        None => default.map(str::to_string),
        Some(raw) => {
            let cleaned = raw.trim();
            if cleaned.is_empty() {
                return default.map(str::to_string);
            }
            if cleaned.starts_with("Expenses:") {
                return Some(normalize_legacy_account_target(cleaned));
            }
            for (key, mapped) in &rule_layers.account_mapping {
                if key == cleaned {
                    return Some(normalize_legacy_account_target(mapped));
                }
            }
            default.map(str::to_string)
        }
    }
}

fn categorize_description(description: &str, rule_layers: &ParserRuleLayers) -> Option<String> {
    let category_key =
        receipt_categories::classify_item_key(description, &rule_layers.category_rules, None);
    resolve_account_target(category_key.as_deref(), rule_layers, None)
}

pub(crate) fn parse_receipt(
    full_text: &str,
    pages_for_helper: &[receipt_parse_helpers::MerchantPageInput],
    pages_for_spatial: &[receipt_spatial::PageInput],
    rule_layers: &ParserRuleLayers,
    image_filename: &str,
    known_merchants: &[String],
    current_year: i32,
) -> ParsedReceiptData {
    let lines = full_text
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(str::to_string)
        .collect::<Vec<_>>();

    let merchant = receipt_parse_helpers::extract_merchant(
        &lines,
        full_text,
        pages_for_helper,
        known_merchants,
    );
    let parsed_date = receipt_fields::extract_date(&lines, full_text, current_year);
    let date = parsed_date.map(|value| (value.year, value.month, value.day));
    let date_is_placeholder = date.is_none();
    let total_cents = receipt_fields::extract_total(&lines);
    let tax_cents = receipt_fields::extract_tax(&lines);
    let subtotal_cents = receipt_fields::extract_subtotal(&lines);

    let mut summary_amounts = HashSet::new();
    if total_cents != 0 {
        summary_amounts.insert(total_cents);
    }
    if let Some(tax_cents) = tax_cents {
        summary_amounts.insert(tax_cents);
    }
    if let Some(subtotal_cents) = subtotal_cents {
        summary_amounts.insert(subtotal_cents);
    }

    let spatial_layout = receipt_parse_helpers::has_useful_bbox_data(pages_for_helper)
        && receipt_parse_helpers::is_spatial_layout_receipt(full_text);

    let (items, warnings) = if spatial_layout {
        let spatial_outcome = receipt_spatial::extract_spatial_items(pages_for_spatial.to_vec());
        if spatial_outcome.items.is_empty() {
            let (items, warnings) = receipt_text::extract_text_items(&lines, &summary_amounts);
            (
                items
                    .into_iter()
                    .map(|item| ParsedReceiptItem {
                        description: item.description.clone(),
                        price: cents_to_fixed(item.price_cents),
                        quantity: item.quantity,
                        category: categorize_description(&item.category_source, rule_layers),
                    })
                    .collect(),
                warnings
                    .into_iter()
                    .map(|warning| ParsedReceiptWarning {
                        message: warning.message,
                        after_item_index: warning.after_item_index,
                    })
                    .collect(),
            )
        } else {
            (
                spatial_outcome
                    .items
                    .into_iter()
                    .map(|item| ParsedReceiptItem {
                        description: item.description.clone(),
                        price: scaled_to_fixed(item.price_scaled, 10_000),
                        quantity: 1,
                        category: categorize_description(&item.description, rule_layers),
                    })
                    .collect(),
                spatial_outcome
                    .warnings
                    .into_iter()
                    .map(|warning| ParsedReceiptWarning {
                        message: warning.message,
                        after_item_index: warning.after_item_index,
                    })
                    .collect(),
            )
        }
    } else {
        let (items, warnings) = receipt_text::extract_text_items(&lines, &summary_amounts);
        (
            items
                .into_iter()
                .map(|item| ParsedReceiptItem {
                    description: item.description.clone(),
                    price: cents_to_fixed(item.price_cents),
                    quantity: item.quantity,
                    category: categorize_description(&item.category_source, rule_layers),
                })
                .collect(),
            warnings
                .into_iter()
                .map(|warning| ParsedReceiptWarning {
                    message: warning.message,
                    after_item_index: warning.after_item_index,
                })
                .collect(),
        )
    };

    ParsedReceiptData {
        merchant,
        date,
        date_is_placeholder,
        total: cents_to_fixed(total_cents),
        items,
        tax: tax_cents.map(cents_to_fixed),
        subtotal: subtotal_cents.map(cents_to_fixed),
        raw_text: full_text.to_string(),
        image_filename: image_filename.to_string(),
        warnings,
    }
}
