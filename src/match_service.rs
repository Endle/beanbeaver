use crate::match_domain::{ApplyMatchResult, MatchCandidate, MatchCandidateRef, ReceiptMatchPlan};
use crate::matcher;
use crate::python_ledger_access::{
    apply_receipt_match_native, list_transactions_native, NativeLedgerPosting,
    NativeLedgerSnapshot, NativeLedgerTransaction,
};
use crate::receipt_formatter::{
    format_enriched_transaction, EnrichedMatchInput, EnrichedPostingInput, FormatterItemInput,
    FormatterReceiptInput, FormatterWarningInput,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyModule};
use std::path::{Path, PathBuf};

const SCALE: i64 = 10_000;

#[derive(Clone, Debug)]
struct NativeReceiptItem {
    description: String,
    price: String,
    quantity: i32,
    category: Option<String>,
}

#[derive(Clone, Debug)]
struct NativeReceiptWarning {
    message: String,
    after_item_index: Option<usize>,
}

#[derive(Clone, Debug)]
struct NativeReceipt {
    merchant: String,
    date_iso: String,
    date_ordinal: i32,
    total: String,
    date_is_placeholder: bool,
    items: Vec<NativeReceiptItem>,
    tax: Option<String>,
    raw_text: String,
    image_filename: String,
    warnings: Vec<NativeReceiptWarning>,
}

#[derive(Clone, Debug)]
struct NativeMerchantFamily {
    canonical: String,
    aliases: Vec<String>,
}

#[derive(Clone, Debug)]
struct MatchResolution {
    candidates: Vec<MatchCandidate>,
    used_relaxed_threshold: bool,
    warning: Option<String>,
}

#[derive(Clone, Debug)]
struct ResolvedCandidate {
    index: usize,
    confidence: f64,
    details: String,
    strength: &'static str,
}

fn fixed_mul(a: i64, b: i64) -> i64 {
    (((a as i128) * (b as i128)) / 10_000_i128) as i64
}

fn max_i64(a: i64, b: i64) -> i64 {
    if a >= b { a } else { b }
}

fn resolve_ledger_path(py: Python<'_>, ledger_path: Option<&str>) -> PyResult<String> {
    if let Some(path) = ledger_path {
        return Ok(path.to_string());
    }

    let runtime = PyModule::import(py, "beanbeaver.runtime")?;
    let paths = runtime.getattr("get_paths")?.call0()?;
    paths.getattr("main_beancount")?.str()?.extract()
}

fn python_path<'py>(py: Python<'py>, raw: &str) -> PyResult<Bound<'py, PyAny>> {
    let pathlib = PyModule::import(py, "pathlib")?;
    pathlib.getattr("Path")?.call1((raw,))
}

fn optional_string(value: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if value.is_none() {
        return Ok(None);
    }
    Ok(Some(value.str()?.extract::<String>()?))
}

fn extract_receipt(py: Python<'_>, approved_receipt_path: &str) -> PyResult<NativeReceipt> {
    let storage = PyModule::import(py, "beanbeaver.runtime.receipt_storage")?;
    let receipt = storage
        .getattr("parse_receipt_from_stage_json")?
        .call1((python_path(py, approved_receipt_path)?,))?;

    let mut items = Vec::new();
    for item in receipt.getattr("items")?.try_iter()? {
        let item = item?;
        items.push(NativeReceiptItem {
            description: item.getattr("description")?.extract::<String>()?,
            price: item.getattr("price")?.str()?.extract::<String>()?,
            quantity: item.getattr("quantity")?.extract::<i32>().unwrap_or(1),
            category: item.getattr("category")?.extract::<Option<String>>()?,
        });
    }

    let mut warnings = Vec::new();
    for warning in receipt.getattr("warnings")?.try_iter()? {
        let warning = warning?;
        warnings.push(NativeReceiptWarning {
            message: warning.getattr("message")?.extract::<String>()?,
            after_item_index: warning
                .getattr("after_item_index")?
                .extract::<Option<usize>>()?,
        });
    }

    let date = receipt.getattr("date")?;
    Ok(NativeReceipt {
        merchant: receipt.getattr("merchant")?.extract::<String>()?,
        date_iso: date.call_method0("isoformat")?.extract::<String>()?,
        date_ordinal: date.call_method0("toordinal")?.extract::<i32>()?,
        total: receipt.getattr("total")?.str()?.extract::<String>()?,
        date_is_placeholder: receipt.getattr("date_is_placeholder")?.extract::<bool>()?,
        items,
        tax: optional_string(&receipt.getattr("tax")?)?,
        raw_text: receipt.getattr("raw_text")?.extract::<String>()?,
        image_filename: receipt.getattr("image_filename")?.extract::<String>()?,
        warnings,
    })
}

fn load_merchant_families(py: Python<'_>) -> PyResult<Vec<NativeMerchantFamily>> {
    let runtime = PyModule::import(py, "beanbeaver.runtime")?;
    let families = runtime.getattr("load_merchant_families")?.call0()?;
    let mut out = Vec::new();
    for family in families.try_iter()? {
        let family = family?;
        out.push(NativeMerchantFamily {
            canonical: family.getattr("canonical")?.extract::<String>()?,
            aliases: family.getattr("aliases")?.extract::<Vec<String>>()?,
        });
    }
    Ok(out)
}

fn decimal_to_scaled(value: &str) -> i64 {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return 0;
    }

    let negative = trimmed.starts_with('-');
    let unsigned = trimmed.trim_start_matches('-');
    let mut parts = unsigned.splitn(2, '.');
    let whole = parts.next().unwrap_or("0").parse::<i64>().unwrap_or(0);
    let frac_raw = parts.next().unwrap_or("0");
    let mut frac = frac_raw.chars().take(4).collect::<String>();
    while frac.len() < 4 {
        frac.push('0');
    }
    let frac_value = frac.parse::<i64>().unwrap_or(0);
    let value = whole * SCALE + frac_value;
    if negative {
        -value
    } else {
        value
    }
}

fn scaled_to_currency(value: i64) -> String {
    format!("{:.2}", (value as f64) / (SCALE as f64))
}

fn receipt_input(receipt: &NativeReceipt) -> matcher::ReceiptInput {
    matcher::ReceiptInput::new(
        receipt.date_ordinal,
        decimal_to_scaled(&receipt.total),
        receipt.merchant.clone(),
        receipt.date_is_placeholder,
    )
}

fn strict_config() -> matcher::MatchConfig {
    matcher::MatchConfig::new(3, 1_000, 100, 3_000)
}

fn relaxed_config() -> matcher::MatchConfig {
    matcher::MatchConfig::new(7, 20_000, 800, 1_500)
}

fn relaxed_amount_tolerance_scaled(receipt_total_scaled: i64) -> i64 {
    max_i64(20_000, fixed_mul(receipt_total_scaled, 800))
}

fn matcher_transactions(snapshot: &NativeLedgerSnapshot) -> Vec<matcher::TransactionInput> {
    snapshot
        .transactions
        .iter()
        .map(|txn| {
            matcher::TransactionInput::new(
                txn.date_ordinal,
                txn.payee.clone(),
                txn.postings
                    .iter()
                    .map(|posting| posting.number_str.as_deref().map(decimal_to_scaled))
                    .collect(),
            )
        })
        .collect()
}

fn matcher_families(families: &[NativeMerchantFamily]) -> Vec<matcher::MerchantFamilyInput> {
    families
        .iter()
        .map(|family| {
            matcher::MerchantFamilyInput::new(family.canonical.clone(), family.aliases.clone())
        })
        .collect()
}

fn first_negative_posting(txn: &NativeLedgerTransaction) -> Option<&NativeLedgerPosting> {
    txn.postings.iter().find(|posting| {
        posting
            .number_str
            .as_deref()
            .map(decimal_to_scaled)
            .is_some_and(|amount| amount < 0)
    })
}

fn transaction_charge_amount_scaled(txn: &NativeLedgerTransaction) -> Option<i64> {
    first_negative_posting(txn).and_then(|posting| {
        posting
            .number_str
            .as_deref()
            .map(decimal_to_scaled)
            .map(i64::abs)
    })
}

fn itemized_receipt_total_scaled(receipt: &NativeReceipt) -> i64 {
    let item_total: i64 = receipt
        .items
        .iter()
        .map(|item| decimal_to_scaled(&item.price))
        .sum();
    let tax = receipt.tax.as_deref().map(decimal_to_scaled).unwrap_or(0);
    item_total + tax
}

fn format_amount_details(delta_scaled: i64) -> String {
    if delta_scaled == 0 {
        "amount: exact match".to_string()
    } else {
        format!("amount: ${} off", scaled_to_currency(delta_scaled.abs()))
    }
}

fn format_merchant_fallback_details(similarity: f64) -> String {
    if similarity <= 0.0 {
        "merchant: no match".to_string()
    } else {
        format!("merchant: weak match ({similarity:.2})")
    }
}

fn manual_review_fallback_candidates(
    receipt: &NativeReceipt,
    snapshot: &NativeLedgerSnapshot,
    merchant_families: &[NativeMerchantFamily],
) -> Vec<ResolvedCandidate> {
    let matcher_families = matcher_families(merchant_families);
    let amount_tolerance = relaxed_amount_tolerance_scaled(decimal_to_scaled(&receipt.total));
    let date_tolerance_days = 7_i32;
    let mut candidates = snapshot
        .transactions
        .iter()
        .enumerate()
        .filter_map(|(index, txn)| {
            let amount = transaction_charge_amount_scaled(txn)?;
            let amount_delta = (amount - decimal_to_scaled(&receipt.total)).abs();
            if amount_delta > amount_tolerance {
                return None;
            }

            let date_delta = (txn.date_ordinal - receipt.date_ordinal).abs();
            if !receipt.date_is_placeholder && date_delta > date_tolerance_days {
                return None;
            }

            let similarity = txn
                .payee
                .as_deref()
                .map(|payee| {
                    matcher::merchant_similarity(&receipt.merchant, payee, matcher_families.clone())
                })
                .unwrap_or(0.0);

            let amount_component = if amount_delta == 0 {
                1.0
            } else {
                1.0 - ((amount_delta as f64) / (amount_tolerance as f64))
            }
            .clamp(0.0, 1.0);
            let date_component = if receipt.date_is_placeholder {
                0.5
            } else {
                1.0 - ((date_delta as f64) / (date_tolerance_days as f64))
            }
            .clamp(0.0, 1.0);
            let confidence =
                (0.45 * amount_component + 0.35 * date_component + 0.20 * similarity).clamp(0.0, 0.75);

            let details = format!(
                "date: {} day(s) off, {}, {}",
                date_delta,
                format_amount_details(amount_delta),
                format_merchant_fallback_details(similarity),
            );

            Some(ResolvedCandidate {
                index,
                confidence,
                details,
                strength: "fallback",
            })
        })
        .collect::<Vec<_>>();

    candidates.sort_by(|left, right| {
        right
            .confidence
            .partial_cmp(&left.confidence)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(left.index.cmp(&right.index))
    });
    candidates
}

fn format_candidate_display(
    txn: &NativeLedgerTransaction,
    confidence: f64,
    details: &str,
) -> String {
    let amount = transaction_charge_amount_scaled(txn).unwrap_or(0);
    let account = first_negative_posting(txn)
        .map(|posting| posting.account.as_str())
        .unwrap_or("None");
    format!(
        "Match found ({:.0}% confidence):\n  File: {}:{}\n  Date: {}\n  Payee: {}\n  Amount: ${}\n  Account: {}\n  Details: {}\n",
        confidence * 100.0,
        txn.file_path,
        txn.line_number,
        txn.date_iso,
        txn.payee.as_deref().unwrap_or("None"),
        scaled_to_currency(amount),
        account,
        details,
    )
}

fn resolve_candidates(
    receipt: &NativeReceipt,
    snapshot: &NativeLedgerSnapshot,
    merchant_families: &[NativeMerchantFamily],
) -> MatchResolution {
    let matcher_txns = matcher_transactions(snapshot);
    let matcher_families = matcher_families(merchant_families);
    let strict = matcher::match_receipt_to_transactions(
        receipt_input(receipt),
        strict_config(),
        matcher_txns.clone(),
        matcher_families.clone(),
    );

    let (resolved, used_relaxed_threshold, warning) = if strict.is_empty() {
        let relaxed = matcher::match_receipt_to_transactions(
            receipt_input(receipt),
            relaxed_config(),
            matcher_txns,
            matcher_families,
        );
        if relaxed.is_empty() {
            let fallback = manual_review_fallback_candidates(receipt, snapshot, merchant_families);
            if fallback.is_empty() {
                (
                    Vec::new(),
                    false,
                    Some(
                        "No reliable matches found, and no weaker fallback candidates were found."
                            .to_string(),
                    ),
                )
            } else {
                (
                    fallback,
                    true,
                    Some(
                        "No reliable or relaxed merchant matches found. Showing amount/date-only candidates for manual review."
                            .to_string(),
                    ),
                )
            }
        } else {
            (
                relaxed
                    .into_iter()
                    .map(|result| {
                        let (index, confidence, details) = result.into_tuple();
                        ResolvedCandidate {
                            index,
                            confidence,
                            details,
                            strength: "relaxed",
                        }
                    })
                    .collect(),
                true,
                Some(
                    "No reliable matches found. Showing weaker candidates for manual review."
                        .to_string(),
                ),
            )
        }
    } else {
        (
            strict
                .into_iter()
                .map(|result| {
                    let (index, confidence, details) = result.into_tuple();
                    ResolvedCandidate {
                        index,
                        confidence,
                        details,
                        strength: "strict",
                    }
                })
                .collect(),
            false,
            None,
        )
    };

    let candidates = resolved
        .into_iter()
        .filter_map(|resolved| {
            let txn = snapshot.transactions.get(resolved.index)?;
            Some(MatchCandidate {
                candidate_ref: MatchCandidateRef {
                    file_path: txn.file_path.clone(),
                    line_number: txn.line_number,
                },
                confidence: resolved.confidence,
                display: format_candidate_display(txn, resolved.confidence, &resolved.details),
                payee: txn.payee.clone(),
                narration: txn.narration.clone(),
                date_iso: txn.date_iso.clone(),
                amount: transaction_charge_amount_scaled(txn).map(scaled_to_currency),
                details: resolved.details,
                strength: resolved.strength.to_string(),
            })
        })
        .collect();

    MatchResolution {
        candidates,
        used_relaxed_threshold,
        warning,
    }
}

fn formatter_receipt_input(receipt: &NativeReceipt) -> FormatterReceiptInput {
    FormatterReceiptInput {
        merchant: receipt.merchant.clone(),
        date_iso: receipt.date_iso.clone(),
        date_is_placeholder: receipt.date_is_placeholder,
        total: receipt.total.clone(),
        tax: receipt.tax.clone(),
        image_filename: receipt.image_filename.clone(),
        raw_text: receipt.raw_text.clone(),
        items: receipt
            .items
            .iter()
            .map(|item| FormatterItemInput {
                description: item.description.clone(),
                price: item.price.clone(),
                quantity: item.quantity,
                posting_account: item
                    .category
                    .clone()
                    .unwrap_or_else(|| "Expenses:FIXME".to_string()),
            })
            .collect(),
        warnings: receipt
            .warnings
            .iter()
            .map(|warning| FormatterWarningInput {
                message: warning.message.clone(),
                after_item_index: warning.after_item_index,
            })
            .collect(),
    }
}

fn formatter_match_input(
    candidate: &MatchCandidate,
    transaction: &NativeLedgerTransaction,
) -> EnrichedMatchInput {
    EnrichedMatchInput {
        transaction_date_iso: transaction.date_iso.clone(),
        payee: transaction.payee.clone().unwrap_or_default(),
        narration: transaction.narration.clone().unwrap_or_default(),
        postings: transaction
            .postings
            .iter()
            .map(|posting| EnrichedPostingInput {
                account: posting.account.clone(),
                number: posting.number_str.clone(),
                currency: posting.currency.clone(),
            })
            .collect(),
        file_path: candidate.candidate_ref.file_path.clone(),
        line_number: candidate.candidate_ref.line_number,
        confidence: candidate.confidence,
        match_details: candidate.details.clone(),
    }
}

fn receipt_chain_name(approved_receipt_path: &str) -> PyResult<String> {
    Path::new(approved_receipt_path)
        .parent()
        .and_then(Path::file_name)
        .map(|name| name.to_string_lossy().into_owned())
        .ok_or_else(|| {
            PyValueError::new_err("Approved receipt path must be inside a receipt directory")
        })
}

fn move_to_matched(py: Python<'_>, approved_receipt_path: &str) -> PyResult<String> {
    let storage = PyModule::import(py, "beanbeaver.runtime.receipt_storage")?;
    let moved = storage
        .getattr("move_to_matched")?
        .call1((python_path(py, approved_receipt_path)?,))?;
    moved.str()?.extract()
}

pub(crate) fn plan_receipt_match(
    py: Python<'_>,
    approved_receipt_path: &str,
    ledger_path: Option<&str>,
) -> PyResult<ReceiptMatchPlan> {
    let resolved_ledger_path = resolve_ledger_path(py, ledger_path)?;
    let ledger_path_buf = PathBuf::from(&resolved_ledger_path);
    if !ledger_path_buf.exists() {
        return Ok(ReceiptMatchPlan {
            receipt_path: approved_receipt_path.to_string(),
            ledger_path: resolved_ledger_path.clone(),
            candidates: Vec::new(),
            errors: vec![format!("Ledger file not found: {resolved_ledger_path}")],
            warning: None,
            used_relaxed_threshold: false,
        });
    }

    let snapshot = list_transactions_native(py, &resolved_ledger_path)?;
    if !snapshot.errors.is_empty() {
        return Ok(ReceiptMatchPlan {
            receipt_path: approved_receipt_path.to_string(),
            ledger_path: resolved_ledger_path,
            candidates: Vec::new(),
            errors: snapshot.errors,
            warning: None,
            used_relaxed_threshold: false,
        });
    }

    let receipt = extract_receipt(py, approved_receipt_path)?;
    let merchant_families = load_merchant_families(py)?;
    let resolution = resolve_candidates(&receipt, &snapshot, &merchant_families);
    Ok(ReceiptMatchPlan {
        receipt_path: approved_receipt_path.to_string(),
        ledger_path: snapshot.path,
        candidates: resolution.candidates,
        errors: Vec::new(),
        warning: resolution.warning,
        used_relaxed_threshold: resolution.used_relaxed_threshold,
    })
}

pub(crate) fn plan_receipt_matches(
    py: Python<'_>,
    approved_receipt_paths: Vec<String>,
    ledger_path: Option<&str>,
) -> PyResult<Vec<ReceiptMatchPlan>> {
    approved_receipt_paths
        .into_iter()
        .map(|path| plan_receipt_match(py, &path, ledger_path))
        .collect()
}

pub(crate) fn apply_receipt_match_service(
    py: Python<'_>,
    approved_receipt_path: &str,
    candidate_file_path: &str,
    candidate_line_number: i32,
    ledger_path: Option<&str>,
) -> PyResult<ApplyMatchResult> {
    let plan = plan_receipt_match(py, approved_receipt_path, ledger_path)?;
    if !plan.errors.is_empty() {
        return Ok(ApplyMatchResult {
            status: if plan.errors[0].starts_with("Ledger file not found:") {
                "ledger_missing".to_string()
            } else {
                "ledger_errors".to_string()
            },
            ledger_path: plan.ledger_path,
            matched_receipt_path: None,
            enriched_path: None,
            message: Some(plan.errors.join("; ")),
        });
    }

    let resolved_ledger_path = plan.ledger_path.clone();
    let snapshot = list_transactions_native(py, &resolved_ledger_path)?;
    let receipt = extract_receipt(py, approved_receipt_path)?;
    let candidate = plan
        .candidates
        .iter()
        .find(|candidate| {
            candidate.candidate_ref.file_path == candidate_file_path
                && candidate.candidate_ref.line_number == candidate_line_number
        })
        .cloned();

    let Some(candidate) = candidate else {
        return Ok(ApplyMatchResult {
            status: "candidate_missing".to_string(),
            ledger_path: resolved_ledger_path,
            matched_receipt_path: None,
            enriched_path: None,
            message: Some("Selected match candidate is no longer available.".to_string()),
        });
    };

    let selected_transaction = snapshot.transactions.iter().find(|txn| {
        txn.file_path == candidate.candidate_ref.file_path
            && txn.line_number == candidate.candidate_ref.line_number
    });
    let Some(selected_transaction) = selected_transaction else {
        return Ok(ApplyMatchResult {
            status: "target_missing".to_string(),
            ledger_path: resolved_ledger_path,
            matched_receipt_path: None,
            enriched_path: None,
            message: Some(format!("Match target file missing: {candidate_file_path}")),
        });
    };

    let matched_file = PathBuf::from(&selected_transaction.file_path);
    if selected_transaction.file_path == "unknown" || !matched_file.exists() {
        return Ok(ApplyMatchResult {
            status: "target_missing".to_string(),
            ledger_path: resolved_ledger_path,
            matched_receipt_path: None,
            enriched_path: None,
            message: Some(format!(
                "Match target file missing: {}",
                selected_transaction.file_path
            )),
        });
    }

    if let Some(expected_total) = transaction_charge_amount_scaled(selected_transaction) {
        let itemized_total = itemized_receipt_total_scaled(&receipt);
        let delta = expected_total - itemized_total;
        if delta < -100 {
            return Ok(ApplyMatchResult {
                status: "receipt_total_exceeds_transaction".to_string(),
                ledger_path: resolved_ledger_path,
                matched_receipt_path: None,
                enriched_path: None,
                message: Some(format!(
                    "Itemized receipt total (${}) exceeds card transaction (${}) by ${}. Re-edit receipt first.",
                    scaled_to_currency(itemized_total),
                    scaled_to_currency(expected_total),
                    scaled_to_currency(delta.abs()),
                )),
            });
        }
    }

    let receipt_name = receipt_chain_name(approved_receipt_path)?;
    let enriched = format_enriched_transaction(
        &formatter_receipt_input(&receipt),
        &formatter_match_input(&candidate, selected_transaction),
        "Expenses:FIXME",
    );
    let enriched_dir = matched_file
        .parent()
        .map(|parent| parent.join("_enriched"))
        .ok_or_else(|| PyValueError::new_err("Matched ledger file must have a parent directory"))?;
    std::fs::create_dir_all(&enriched_dir)?;
    let enriched_path = enriched_dir.join(format!("{receipt_name}.beancount"));
    let matched_file_parent = matched_file
        .parent()
        .ok_or_else(|| PyValueError::new_err("Matched ledger file must have a parent directory"))?;
    let include_rel = enriched_path
        .strip_prefix(matched_file_parent)
        .map_err(|_| PyValueError::new_err("Failed to compute include path for enriched file"))?
        .to_string_lossy()
        .replace('\\', "/");

    let status = apply_receipt_match_native(
        py,
        &resolved_ledger_path,
        &selected_transaction.file_path,
        selected_transaction.line_number as usize,
        &include_rel,
        &receipt_name,
        &enriched_path.to_string_lossy(),
        &enriched,
    )?;
    let matched_receipt_path = move_to_matched(py, approved_receipt_path)?;
    let action_msg = if status == "already_applied" {
        "already applied; receipt archived"
    } else {
        "applied"
    };
    let weak_prefix = if plan.used_relaxed_threshold {
        "Weak candidate applied after relaxed fallback. "
    } else {
        ""
    };

    Ok(ApplyMatchResult {
        status,
        ledger_path: resolved_ledger_path,
        matched_receipt_path: Some(matched_receipt_path),
        enriched_path: Some(enriched_path.to_string_lossy().into_owned()),
        message: Some(format!(
            "{weak_prefix}Transaction {action_msg}. Enriched file: {}",
            enriched_path.to_string_lossy()
        )),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::python_ledger_access::{NativeLedgerPosting, NativeLedgerSnapshot, NativeLedgerTransaction};

    #[test]
    fn resolve_candidates_surfaces_amount_date_only_fallback_when_merchant_match_is_zero() {
        let receipt = NativeReceipt {
            merchant: "FRESH".to_string(),
            date_iso: "2026-03-03".to_string(),
            date_ordinal: 739313,
            total: "91.22".to_string(),
            date_is_placeholder: false,
            items: Vec::new(),
            tax: Some("0.00".to_string()),
            raw_text: String::new(),
            image_filename: String::new(),
            warnings: Vec::new(),
        };
        let snapshot = NativeLedgerSnapshot {
            path: "/tmp/main.beancount".to_string(),
            transactions: vec![NativeLedgerTransaction {
                date_ordinal: 739314,
                date_iso: "2026-03-04".to_string(),
                payee: Some("FOODY MART MARKHAM ON".to_string()),
                narration: Some(String::new()),
                postings: vec![NativeLedgerPosting {
                    account: "Liabilities:CreditCard:CardA".to_string(),
                    number_str: Some("-91.22".to_string()),
                    currency: Some("CAD".to_string()),
                }],
                file_path: "/tmp/records/2026/carda.beancount".to_string(),
                line_number: 42,
            }],
            errors: Vec::new(),
            options: std::collections::HashMap::new(),
        };

        let resolved = resolve_candidates(&receipt, &snapshot, &[]);

        assert!(resolved.used_relaxed_threshold);
        assert_eq!(
            resolved.warning.as_deref(),
            Some(
                "No reliable or relaxed merchant matches found. Showing amount/date-only candidates for manual review."
            )
        );
        assert_eq!(resolved.candidates.len(), 1);
        assert_eq!(resolved.candidates[0].strength, "fallback");
        assert_eq!(resolved.candidates[0].date_iso, "2026-03-04");
    }
}
