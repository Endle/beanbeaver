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

fn merchant_similarity_impl(receipt_merchant: &str, txn_payee: &str) -> f64 {
    let normalized_receipt = normalize_merchant(receipt_merchant);
    let normalized_txn = normalize_merchant(txn_payee);

    if normalized_receipt.is_empty() || normalized_txn.is_empty() {
        return 0.0;
    }

    if normalized_txn.contains(&normalized_receipt) || normalized_receipt.contains(&normalized_txn) {
        return 0.9;
    }

    let common_prefix = normalized_receipt
        .chars()
        .zip(normalized_txn.chars())
        .take_while(|(left, right)| left == right)
        .count();
    let min_len = normalized_receipt.len().min(normalized_txn.len());
    if common_prefix >= 4 && min_len > 0 {
        return 0.5 + 0.4 * ((common_prefix as f64) / (min_len as f64));
    }

    let receipt_words = alpha_words(receipt_merchant);
    let txn_words = alpha_words(txn_payee);
    if !receipt_words.is_empty() && !txn_words.is_empty() {
        let common_words = receipt_words.intersection(&txn_words).count();
        if common_words > 0 {
            let union_count = receipt_words.union(&txn_words).count();
            if union_count > 0 {
                return 0.3 + 0.4 * ((common_words as f64) / (union_count as f64));
            }
        }
    }

    0.0
}

fn match_receipt_to_transaction_impl(
    receipt: &ReceiptInput,
    txn: &TransactionInput,
    config: &MatchConfig,
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
            confidence += 0.4 * (1.0 - (date_diff as f64) / ((config.date_tolerance_days + 1) as f64));
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

    let merchant_score = merchant_similarity_impl(&receipt.merchant, txn.payee.as_deref().unwrap_or(""));
    if merchant_score < 0.3 {
        return None;
    }

    confidence += 0.2 * merchant_score;
    if merchant_score > 0.8 {
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
            confidence += 0.4 * (1.0 - (date_diff as f64) / ((config.date_tolerance_days + 1) as f64));
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

    let merchant_score = merchant_similarity_impl(&receipt.merchant, txn_payee);
    if merchant_score < 0.3 {
        return None;
    }

    confidence += 0.2 * merchant_score;
    if merchant_score > 0.8 {
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
fn merchant_similarity(receipt_merchant: &str, txn_payee: &str) -> f64 {
    merchant_similarity_impl(receipt_merchant, txn_payee)
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

    let mut matches: Vec<(usize, f64, String)> = transactions
        .into_iter()
        .enumerate()
        .filter_map(|(index, (date_ordinal, payee, posting_amounts_scaled))| {
            let txn = TransactionInput {
                date_ordinal,
                payee,
                posting_amounts_scaled,
            };
            match_receipt_to_transaction_impl(&receipt, &txn, &config)
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
) -> Vec<(usize, f64, String)> {
    let config = MatchConfig {
        date_tolerance_days,
        amount_tolerance_scaled,
        amount_tolerance_percent_scaled,
    };

    let mut matches: Vec<(usize, f64, String)> = candidates
        .into_iter()
        .enumerate()
        .filter_map(|(index, (date_ordinal, total_scaled, merchant, date_is_placeholder))| {
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
            )
            .map(|(confidence, details)| (index, confidence, details))
        })
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

    #[test]
    fn merchant_similarity_handles_common_substrings() {
        let score = merchant_similarity_impl("T&T", "T&T SUPERMARKET");
        assert!(score > 0.8);
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
        assert!(match_receipt_to_transaction_impl(&receipt, &txn, &default_config()).is_none());
    }
}
