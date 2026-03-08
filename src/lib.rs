mod matcher;

use pyo3::prelude::*;

#[derive(FromPyObject)]
struct PyMatchConfig {
    #[pyo3(item("date_tolerance_days"))]
    date_tolerance_days: i32,
    #[pyo3(item("amount_tolerance_scaled"))]
    amount_tolerance_scaled: i64,
    #[pyo3(item("amount_tolerance_percent_scaled"))]
    amount_tolerance_percent_scaled: i64,
}

#[derive(FromPyObject)]
struct PyReceiptInput {
    #[pyo3(item("date_ordinal"))]
    date_ordinal: i32,
    #[pyo3(item("total_scaled"))]
    total_scaled: i64,
    #[pyo3(item("merchant"))]
    merchant: String,
    #[pyo3(item("date_is_placeholder"))]
    date_is_placeholder: bool,
}

#[derive(FromPyObject)]
struct PyTransactionInput {
    #[pyo3(item("date_ordinal"))]
    date_ordinal: i32,
    #[pyo3(item("payee"))]
    payee: Option<String>,
    #[pyo3(item("posting_amounts_scaled"))]
    posting_amounts_scaled: Vec<Option<i64>>,
}

#[derive(FromPyObject)]
struct PyTransactionQueryInput {
    #[pyo3(item("date_ordinal"))]
    date_ordinal: i32,
    #[pyo3(item("amount_scaled"))]
    amount_scaled: i64,
    #[pyo3(item("payee"))]
    payee: String,
}

#[derive(FromPyObject)]
struct PyMerchantFamilyInput {
    #[pyo3(item("canonical"))]
    canonical: String,
    #[pyo3(item("aliases"))]
    aliases: Vec<String>,
}

fn to_match_config(config: PyMatchConfig) -> matcher::MatchConfig {
    matcher::MatchConfig::new(
        config.date_tolerance_days,
        config.amount_tolerance_scaled,
        config.amount_tolerance_percent_scaled,
    )
}

fn to_receipt_input(receipt: PyReceiptInput) -> matcher::ReceiptInput {
    matcher::ReceiptInput::new(
        receipt.date_ordinal,
        receipt.total_scaled,
        receipt.merchant,
        receipt.date_is_placeholder,
    )
}

fn to_transaction_input(transaction: PyTransactionInput) -> matcher::TransactionInput {
    matcher::TransactionInput::new(
        transaction.date_ordinal,
        transaction.payee,
        transaction.posting_amounts_scaled,
    )
}

fn to_transaction_query_input(transaction: PyTransactionQueryInput) -> matcher::TransactionQueryInput {
    matcher::TransactionQueryInput::new(
        transaction.date_ordinal,
        transaction.amount_scaled,
        transaction.payee,
    )
}

fn to_merchant_family_input(family: PyMerchantFamilyInput) -> matcher::MerchantFamilyInput {
    matcher::MerchantFamilyInput::new(family.canonical, family.aliases)
}

#[pyfunction]
fn merchant_similarity(
    receipt_merchant: &str,
    txn_payee: &str,
    merchant_families: Vec<PyMerchantFamilyInput>,
) -> f64 {
    matcher::merchant_similarity(
        receipt_merchant,
        txn_payee,
        merchant_families
            .into_iter()
            .map(to_merchant_family_input)
            .collect(),
    )
}

#[pyfunction]
fn match_receipt_to_transactions(
    receipt: PyReceiptInput,
    config: PyMatchConfig,
    transactions: Vec<PyTransactionInput>,
    merchant_families: Vec<PyMerchantFamilyInput>,
) -> Vec<(usize, f64, String)> {
    matcher::match_receipt_to_transactions(
        to_receipt_input(receipt),
        to_match_config(config),
        transactions.into_iter().map(to_transaction_input).collect(),
        merchant_families
            .into_iter()
            .map(to_merchant_family_input)
            .collect(),
    )
    .into_iter()
    .map(matcher::MatchResult::into_tuple)
    .collect()
}

#[pyfunction]
fn match_transaction_to_receipts(
    transaction: PyTransactionQueryInput,
    config: PyMatchConfig,
    candidates: Vec<PyReceiptInput>,
    merchant_families: Vec<PyMerchantFamilyInput>,
) -> Vec<(usize, f64, String)> {
    matcher::match_transaction_to_receipts(
        to_transaction_query_input(transaction),
        to_match_config(config),
        candidates.into_iter().map(to_receipt_input).collect(),
        merchant_families
            .into_iter()
            .map(to_merchant_family_input)
            .collect(),
    )
    .into_iter()
    .map(matcher::MatchResult::into_tuple)
    .collect()
}

#[pymodule]
fn _rust_matcher(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(merchant_similarity, module)?)?;
    module.add_function(wrap_pyfunction!(match_receipt_to_transactions, module)?)?;
    module.add_function(wrap_pyfunction!(match_transaction_to_receipts, module)?)?;
    Ok(())
}
