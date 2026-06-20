mod match_domain;
mod match_service;
mod matcher;
mod python_detection_normalization;
mod python_ledger_access;
mod python_ocr_line_grouping;
mod python_match_service;
mod python_receipt_categories;
mod python_receipt_common;
mod python_receipt_fields;
mod python_receipt_formatter;
mod python_receipt_parse_helpers;
mod python_receipt_parser;
mod python_receipt_process;
mod python_receipt_spatial;
mod python_receipt_staged_json;
mod python_receipt_text;
mod python_spatial;

// Pure receipt-parsing logic now lives in the standalone `receipt-core` crate
// (no PyO3 / no GPL beancount dependency). The PyO3 glue modules below reference
// it via `receipt_core::…`.

use pyo3::prelude::*;

#[derive(FromPyObject)]
struct PyMatchConfig {
    #[pyo3(item("date_tolerance_days"))]
    date_tolerance_days: i32,
    #[pyo3(item("amount_tolerance_scaled"))]
    amount_tolerance_scaled: i64,
    #[pyo3(item("amount_tolerance_percent_scaled"))]
    amount_tolerance_percent_scaled: i64,
    #[pyo3(item("merchant_min_similarity_scaled"))]
    merchant_min_similarity_scaled: i64,
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
    #[pyo3(item("card_amount_scaled"), default)]
    card_amount_scaled: Option<i64>,
    #[pyo3(item("non_card_amount_scaled"), default)]
    non_card_amount_scaled: i64,
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
        config.merchant_min_similarity_scaled,
    )
}

fn to_receipt_input(receipt: PyReceiptInput) -> matcher::ReceiptInput {
    matcher::ReceiptInput::with_split_tender(
        receipt.date_ordinal,
        receipt.total_scaled,
        receipt.merchant,
        receipt.date_is_placeholder,
        receipt.card_amount_scaled,
        receipt.non_card_amount_scaled,
    )
}

fn to_transaction_input(transaction: PyTransactionInput) -> matcher::TransactionInput {
    matcher::TransactionInput::new(
        transaction.date_ordinal,
        transaction.payee,
        transaction.posting_amounts_scaled,
    )
}

fn to_transaction_query_input(
    transaction: PyTransactionQueryInput,
) -> matcher::TransactionQueryInput {
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
    python_match_service::register(module)?;
    python_ledger_access::register(module)?;
    python_detection_normalization::register(module)?;
    python_ocr_line_grouping::register(module)?;
    python_receipt_categories::register(module)?;
    python_receipt_common::register(module)?;
    python_receipt_formatter::register(module)?;
    python_receipt_fields::register(module)?;
    python_receipt_parser::register(module)?;
    python_receipt_process::register(module)?;
    python_receipt_parse_helpers::register(module)?;
    python_receipt_staged_json::register(module)?;
    python_spatial::register(module)?;
    python_receipt_spatial::register(module)?;
    python_receipt_text::register(module)?;
    Ok(())
}
