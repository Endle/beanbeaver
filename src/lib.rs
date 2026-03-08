mod matcher;

use pyo3::prelude::*;

use crate::matcher::{
    build_merchant_families, compare_matches, match_receipt_to_transaction_impl,
    match_transaction_to_receipt_impl, merchant_similarity_impl, MatchConfig, ReceiptInput,
    TransactionInput,
};

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

    matches.sort_by(compare_matches);
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

    matches.sort_by(compare_matches);
    matches
}

#[pymodule]
fn _rust_matcher(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(merchant_similarity, module)?)?;
    module.add_function(wrap_pyfunction!(match_receipt_to_transactions, module)?)?;
    module.add_function(wrap_pyfunction!(match_transaction_to_receipts, module)?)?;
    Ok(())
}
