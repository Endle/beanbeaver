mod matcher;

use pyo3::prelude::*;

#[pyfunction]
fn merchant_similarity(
    receipt_merchant: &str,
    txn_payee: &str,
    merchant_families: Vec<(String, Vec<String>)>,
) -> f64 {
    matcher::merchant_similarity(receipt_merchant, txn_payee, merchant_families)
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
    matcher::match_receipt_to_transactions(
        receipt_date_ordinal,
        receipt_total_scaled,
        receipt_merchant,
        receipt_date_is_placeholder,
        date_tolerance_days,
        amount_tolerance_scaled,
        amount_tolerance_percent_scaled,
        transactions,
        merchant_families,
    )
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
    matcher::match_transaction_to_receipts(
        txn_date_ordinal,
        txn_amount_scaled,
        txn_payee,
        date_tolerance_days,
        amount_tolerance_scaled,
        amount_tolerance_percent_scaled,
        candidates,
        merchant_families,
    )
}

#[pymodule]
fn _rust_matcher(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(merchant_similarity, module)?)?;
    module.add_function(wrap_pyfunction!(match_receipt_to_transactions, module)?)?;
    module.add_function(wrap_pyfunction!(match_transaction_to_receipts, module)?)?;
    Ok(())
}
