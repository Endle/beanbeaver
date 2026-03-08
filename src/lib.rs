use std::cmp::Ordering;
use std::collections::HashSet;

use pyo3::prelude::*;

const SCALE: i64 = 10_000;

#[derive(Clone, Debug)]
struct MatchConfig {
    date_tolerance_days: i32,
    amount_tolerance_scaled: i64,
    amount_tolerance_percent_scaled: i64,
}

#[derive(Clone, Debug)]
struct ReceiptInput {
    date_ordinal: i32,
    total_scaled: i64,
    merchant: String,
    date_is_placeholder: bool,
}

#[derive(Clone, Debug)]
struct TransactionInput {
    date_ordinal: i32,
    payee: Option<String>,
    posting_amounts_scaled: Vec<Option<i64>>,
}

#[derive(Clone, Debug)]
struct MerchantFamily {
    canonical_label: String,
    canonical_normalized: String,
    aliases_normalized: Vec<String>,
}

fn fixed_mul(a: i64, b: i64) -> i64 {
    (((a as i128) * (b as i128)) / (SCALE as i128)) as i64
}

fn max_i64(a: i64, b: i64) -> i64 {
    if a >= b {
        a
    } else {
        b
    }
}

fn amount_tolerance_scaled(receipt_total_scaled: i64, config: &MatchConfig) -> i64 {
    max_i64(
        config.amount_tolerance_scaled,
        fixed_mul(receipt_total_scaled, config.amount_tolerance_percent_scaled),
    )
}

fn format_scaled_currency(value: i64) -> String {
    format!("{:.2}", (value as f64) / (SCALE as f64))
}

fn normalize_merchant(value: &str) -> String {
    let mut normalized = value.trim().to_ascii_uppercase();

    if let Some(stripped) = strip_noise_suffix(&normalized) {
        normalized = stripped;
    }
    if let Some(stripped) = strip_state_suffix(&normalized) {
        normalized = stripped;
    }
    if let Some(stripped) = strip_trailing_city_like(&normalized) {
        normalized = stripped;
    }

    normalized
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .collect()
}

fn strip_noise_suffix(value: &str) -> Option<String> {
    let trimmed = value.trim_end();
    let tokens: Vec<&str> = trimmed.split_whitespace().collect();
    if tokens.len() < 2 {
        return None;
    }
    let last = tokens.last()?.trim_end_matches('.');
    let is_noise = matches!(last, "INC" | "LLC" | "LTD" | "CORP" | "CO")
        || last.chars().all(|ch| ch.is_ascii_digit())
        || (last.starts_with('#') && last[1..].chars().all(|ch| ch.is_ascii_digit()));
    if is_noise {
        Some(tokens[..tokens.len() - 1].join(" "))
    } else {
        None
    }
}

fn strip_state_suffix(value: &str) -> Option<String> {
    let trimmed = value.trim_end();
    if trimmed.len() < 2 {
        return None;
    }
    let suffix = &trimmed[trimmed.len() - 2..];
    if !suffix.chars().all(|ch| ch.is_ascii_uppercase()) {
        return None;
    }
    let prefix = &trimmed[..trimmed.len() - 2];
    let stripped = prefix.trim_end_matches([',', ' ']);
    if stripped.len() == prefix.len() {
        return None;
    }
    Some(stripped.trim_end().to_string())
}

fn strip_trailing_city_like(value: &str) -> Option<String> {
    let trimmed = value.trim_end();
    let mut end = trimmed.len();
    while end > 0 && trimmed.as_bytes()[end - 1].is_ascii_whitespace() {
        end -= 1;
    }
    let token_end = end;
    while end > 0 && trimmed.as_bytes()[end - 1].is_ascii_alphabetic() {
        end -= 1;
    }
    if token_end == end {
        return None;
    }
    let token = &trimmed[end..token_end];
    if token.len() < 2 {
        return None;
    }
    let separator = trimmed[..end].chars().last()?;
    if separator != ' ' && separator != ',' {
        return None;
    }
    let stripped = trimmed[..end].trim_end_matches([',', ' ']).trim_end();
    if stripped.is_empty() {
        return None;
    }
    Some(stripped.to_string())
}

fn alpha_words(value: &str) -> HashSet<String> {
    value
        .to_ascii_uppercase()
        .split(|ch: char| !ch.is_ascii_alphabetic())
        .filter(|word| word.len() >= 3)
        .map(str::to_string)
        .collect()
}

fn build_merchant_families(raw_families: &[(String, Vec<String>)]) -> Vec<MerchantFamily> {
    raw_families
        .iter()
        .filter_map(|(canonical, aliases)| {
            let canonical_normalized = normalize_merchant(canonical);
            if canonical_normalized.is_empty() {
                return None;
            }
            let aliases_normalized = std::iter::once(canonical.clone())
                .chain(aliases.iter().cloned())
                .map(|alias| normalize_merchant(&alias))
                .filter(|alias| !alias.is_empty())
                .collect();
            Some(MerchantFamily {
                canonical_label: canonical.clone(),
                canonical_normalized,
                aliases_normalized,
            })
        })
        .collect()
}

fn alias_matches(normalized_value: &str, normalized_alias: &str) -> bool {
    normalized_value == normalized_alias
        || normalized_value.contains(normalized_alias)
        || normalized_alias.contains(normalized_value)
}

fn canonicalize_merchant(value: &str, families: &[MerchantFamily]) -> (String, Option<String>) {
    let normalized_value = normalize_merchant(value);
    if normalized_value.is_empty() {
        return (normalized_value, None);
    }

    for family in families {
        if family
            .aliases_normalized
            .iter()
            .any(|alias| alias_matches(&normalized_value, alias))
        {
            return (
                family.canonical_normalized.clone(),
                Some(family.canonical_label.clone()),
            );
        }
    }

    (normalized_value, None)
}

fn merchant_similarity_impl(
    receipt_merchant: &str,
    txn_payee: &str,
    families: &[MerchantFamily],
) -> (f64, Option<String>) {
    let (normalized_receipt, receipt_family) = canonicalize_merchant(receipt_merchant, families);
    let (normalized_txn, txn_family) = canonicalize_merchant(txn_payee, families);

    if normalized_receipt.is_empty() || normalized_txn.is_empty() {
        return (0.0, None);
    }

    if normalized_receipt == normalized_txn && (receipt_family.is_some() || txn_family.is_some()) {
        return (1.0, receipt_family.or(txn_family));
    }

    if normalized_txn.contains(&normalized_receipt) || normalized_receipt.contains(&normalized_txn)
    {
        return (0.9, None);
    }

    let common_prefix = normalized_receipt
        .chars()
        .zip(normalized_txn.chars())
        .take_while(|(left, right)| left == right)
        .count();
    let min_len = normalized_receipt.len().min(normalized_txn.len());
    if common_prefix >= 4 && min_len > 0 {
        return (
            0.5 + 0.4 * ((common_prefix as f64) / (min_len as f64)),
            None,
        );
    }

    let receipt_words = alpha_words(receipt_merchant);
    let txn_words = alpha_words(txn_payee);
    if !receipt_words.is_empty() && !txn_words.is_empty() {
        let common_words = receipt_words.intersection(&txn_words).count();
        if common_words > 0 {
            let union_count = receipt_words.union(&txn_words).count();
            if union_count > 0 {
                return (
                    0.3 + 0.4 * ((common_words as f64) / (union_count as f64)),
                    None,
                );
            }
        }
    }

    (0.0, None)
}

fn match_receipt_to_transaction_impl(
    receipt: &ReceiptInput,
    txn: &TransactionInput,
    config: &MatchConfig,
    families: &[MerchantFamily],
) -> Option<(f64, String)> {
    let mut confidence = 0.0;
    let mut details: Vec<String> = Vec::new();

    if receipt.date_is_placeholder {
        details.push("date: unknown".to_string());
    } else {
        let date_diff = (txn.date_ordinal - receipt.date_ordinal).abs();
        if date_diff > config.date_tolerance_days {
            return None;
        }
        if date_diff == 0 {
            confidence += 0.4;
            details.push("date: exact match".to_string());
        } else {
            confidence +=
                0.4 * (1.0 - (date_diff as f64) / ((config.date_tolerance_days + 1) as f64));
            details.push(format!("date: {date_diff} day(s) off"));
        }
    }

    let txn_amount_scaled = txn
        .posting_amounts_scaled
        .iter()
        .flatten()
        .find_map(|value| if *value < 0 { Some(value.abs()) } else { None })?;

    let amount_diff_scaled = (txn_amount_scaled - receipt.total_scaled).abs();
    let amount_tolerance_scaled = amount_tolerance_scaled(receipt.total_scaled, config);
    if amount_diff_scaled > amount_tolerance_scaled {
        return None;
    }
    if amount_diff_scaled == 0 {
        confidence += 0.4;
        details.push("amount: exact match".to_string());
    } else {
        confidence += 0.4 * (1.0 - (amount_diff_scaled as f64) / (amount_tolerance_scaled as f64));
        details.push(format!(
            "amount: ${} off",
            format_scaled_currency(amount_diff_scaled)
        ));
    }

    let (merchant_score, matched_family) = merchant_similarity_impl(
        &receipt.merchant,
        txn.payee.as_deref().unwrap_or(""),
        families,
    );
    if merchant_score < 0.3 {
        return None;
    }

    confidence += 0.2 * merchant_score;
    if let Some(family) = matched_family {
        details.push(format!("merchant: family match ({family})"));
    } else if merchant_score > 0.8 {
        details.push("merchant: good match".to_string());
    } else {
        details.push(format!(
            "merchant: partial match ({:.0}%)",
            merchant_score * 100.0
        ));
    }

    Some((confidence, details.join(", ")))
}

fn match_transaction_to_receipt_impl(
    txn_date_ordinal: i32,
    txn_amount_scaled: i64,
    txn_payee: &str,
    receipt: &ReceiptInput,
    config: &MatchConfig,
    families: &[MerchantFamily],
) -> Option<(f64, String)> {
    let mut confidence = 0.0;
    let mut details: Vec<String> = Vec::new();

    if receipt.date_is_placeholder {
        details.push("date: unknown".to_string());
    } else {
        let date_diff = (txn_date_ordinal - receipt.date_ordinal).abs();
        if date_diff > config.date_tolerance_days {
            return None;
        }
        if date_diff == 0 {
            confidence += 0.4;
            details.push("date: exact match".to_string());
        } else {
            confidence +=
                0.4 * (1.0 - (date_diff as f64) / ((config.date_tolerance_days + 1) as f64));
            details.push(format!("date: {date_diff} day(s) off"));
        }
    }

    let amount_diff_scaled = (txn_amount_scaled - receipt.total_scaled).abs();
    let amount_tolerance_scaled = amount_tolerance_scaled(receipt.total_scaled, config);
    if amount_diff_scaled > amount_tolerance_scaled {
        return None;
    }
    if amount_diff_scaled == 0 {
        confidence += 0.4;
        details.push("amount: exact match".to_string());
    } else {
        confidence += 0.4 * (1.0 - (amount_diff_scaled as f64) / (amount_tolerance_scaled as f64));
        details.push(format!(
            "amount: ${} off",
            format_scaled_currency(amount_diff_scaled)
        ));
    }

    let (merchant_score, matched_family) =
        merchant_similarity_impl(&receipt.merchant, txn_payee, families);
    if merchant_score < 0.3 {
        return None;
    }

    confidence += 0.2 * merchant_score;
    if let Some(family) = matched_family {
        details.push(format!("merchant: family match ({family})"));
    } else if merchant_score > 0.8 {
        details.push("merchant: good match".to_string());
    } else {
        details.push(format!(
            "merchant: partial match ({:.0}%)",
            merchant_score * 100.0
        ));
    }

    Some((confidence, details.join(", ")))
}

#[pyfunction]
fn merchant_similarity(
    receipt_merchant: &str,
    txn_payee: &str,
    merchant_families: Vec<(String, Vec<String>)>,
) -> f64 {
    let families = build_merchant_families(&merchant_families);
    merchant_similarity_impl(receipt_merchant, txn_payee, &families).0
}

#[pyfunction]
fn match_receipt_to_transactions(
    receipt_date_ordinal: i32,
    receipt_total_scaled: i64,
    receipt_merchant: String,
    receipt_date_is_placeholder: bool,
    date_tolerance_days: i32,
    amount_tolerance_scaled: i64,
    amount_tolerance_percent_scaled: i64,
    transactions: Vec<(i32, Option<String>, Vec<Option<i64>>)>,
    merchant_families: Vec<(String, Vec<String>)>,
) -> Vec<(usize, f64, String)> {
    let receipt = ReceiptInput {
        date_ordinal: receipt_date_ordinal,
        total_scaled: receipt_total_scaled,
        merchant: receipt_merchant,
        date_is_placeholder: receipt_date_is_placeholder,
    };
    let config = MatchConfig {
        date_tolerance_days,
        amount_tolerance_scaled,
        amount_tolerance_percent_scaled,
    };
    let families = build_merchant_families(&merchant_families);

    let mut matches: Vec<(usize, f64, String)> = transactions
        .into_iter()
        .enumerate()
        .filter_map(|(index, (date_ordinal, payee, posting_amounts_scaled))| {
            let txn = TransactionInput {
                date_ordinal,
                payee,
                posting_amounts_scaled,
            };
            match_receipt_to_transaction_impl(&receipt, &txn, &config, &families)
                .map(|(confidence, details)| (index, confidence, details))
        })
        .collect();

    matches.sort_by(|left, right| compare_matches(left, right));
    matches
}

#[pyfunction]
fn match_transaction_to_receipts(
    txn_date_ordinal: i32,
    txn_amount_scaled: i64,
    txn_payee: String,
    date_tolerance_days: i32,
    amount_tolerance_scaled: i64,
    amount_tolerance_percent_scaled: i64,
    candidates: Vec<(i32, i64, String, bool)>,
    merchant_families: Vec<(String, Vec<String>)>,
) -> Vec<(usize, f64, String)> {
    let config = MatchConfig {
        date_tolerance_days,
        amount_tolerance_scaled,
        amount_tolerance_percent_scaled,
    };
    let families = build_merchant_families(&merchant_families);

    let mut matches: Vec<(usize, f64, String)> = candidates
        .into_iter()
        .enumerate()
        .filter_map(
            |(index, (date_ordinal, total_scaled, merchant, date_is_placeholder))| {
                let receipt = ReceiptInput {
                    date_ordinal,
                    total_scaled,
                    merchant,
                    date_is_placeholder,
                };
                match_transaction_to_receipt_impl(
                    txn_date_ordinal,
                    txn_amount_scaled,
                    &txn_payee,
                    &receipt,
                    &config,
                    &families,
                )
                .map(|(confidence, details)| (index, confidence, details))
            },
        )
        .collect();

    matches.sort_by(|left, right| compare_matches(left, right));
    matches
}

fn compare_matches(left: &(usize, f64, String), right: &(usize, f64, String)) -> Ordering {
    right
        .1
        .partial_cmp(&left.1)
        .unwrap_or(Ordering::Equal)
        .then(left.0.cmp(&right.0))
}

#[pymodule]
fn _rust_matcher(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(merchant_similarity, module)?)?;
    module.add_function(wrap_pyfunction!(match_receipt_to_transactions, module)?)?;
    module.add_function(wrap_pyfunction!(match_transaction_to_receipts, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn default_config() -> MatchConfig {
        MatchConfig {
            date_tolerance_days: 3,
            amount_tolerance_scaled: 1_000,
            amount_tolerance_percent_scaled: 100,
        }
    }

    fn merchant_families() -> Vec<MerchantFamily> {
        build_merchant_families(&[(
            "REAL CANADIAN SUPERSTORE".to_string(),
            vec!["REAL CANADIAN".to_string(), "RCSS".to_string()],
        )])
    }

    #[test]
    fn merchant_similarity_handles_common_substrings() {
        let score = merchant_similarity_impl("T&T", "T&T SUPERMARKET", &[]).0;
        assert!(score > 0.8);
    }

    #[test]
    fn merchant_similarity_handles_family_aliases() {
        let (score, family) = merchant_similarity_impl(
            "REAL CANADIAN",
            "RCSS 1077 TORONTO ON",
            &merchant_families(),
        );
        assert!(score > 0.8);
        assert_eq!(family.as_deref(), Some("REAL CANADIAN SUPERSTORE"));
    }

    #[test]
    fn receipt_transaction_matching_returns_none_for_positive_amounts() {
        let receipt = ReceiptInput {
            date_ordinal: 738_900,
            total_scaled: 1_000_000,
            merchant: "T&T".to_string(),
            date_is_placeholder: false,
        };
        let txn = TransactionInput {
            date_ordinal: 738_900,
            payee: Some("T&T SUPERMARKET".to_string()),
            posting_amounts_scaled: vec![Some(1_000_000)],
        };
        assert!(
            match_receipt_to_transaction_impl(&receipt, &txn, &default_config(), &[]).is_none()
        );
    }
}
