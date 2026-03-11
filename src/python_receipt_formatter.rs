use pyo3::prelude::*;
use pyo3::wrap_pyfunction;

use crate::receipt_formatter;

fn fixed_decimal_string(value: &Bound<'_, PyAny>) -> PyResult<String> {
    value.call_method1("__format__", (".2f",))?.extract::<String>()
}

fn optional_fixed_decimal_string(value: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if value.is_none() {
        return Ok(None);
    }
    Ok(Some(fixed_decimal_string(value)?))
}

fn required_string_attr(obj: &Bound<'_, PyAny>, attr: &str) -> PyResult<String> {
    obj.getattr(attr)?.extract::<String>()
}

fn extract_formatter_receipt_input(
    receipt: &Bound<'_, PyAny>,
    item_accounts: Vec<String>,
) -> PyResult<receipt_formatter::FormatterReceiptInput> {
    let items_any = receipt.getattr("items")?;
    let mut items = Vec::new();
    for (idx, item) in items_any.try_iter()?.enumerate() {
        let item = item?;
        let posting_account = item_accounts
            .get(idx)
            .cloned()
            .unwrap_or_else(|| "Expenses:FIXME".to_string());
        items.push(receipt_formatter::FormatterItemInput {
            description: required_string_attr(&item, "description")?,
            price: fixed_decimal_string(&item.getattr("price")?)?,
            quantity: item.getattr("quantity")?.extract::<i32>().unwrap_or(1),
            posting_account,
        });
    }

    let warnings_any = receipt.getattr("warnings")?;
    let mut warnings = Vec::new();
    for warning in warnings_any.try_iter()? {
        let warning = warning?;
        let after_item_index = match warning.getattr("after_item_index") {
            Ok(value) if !value.is_none() => value.extract::<usize>().ok(),
            _ => None,
        };
        warnings.push(receipt_formatter::FormatterWarningInput {
            message: required_string_attr(&warning, "message")?,
            after_item_index,
        });
    }

    Ok(receipt_formatter::FormatterReceiptInput {
        merchant: required_string_attr(receipt, "merchant")?,
        date_iso: receipt.getattr("date")?.str()?.extract::<String>()?,
        date_is_placeholder: receipt.getattr("date_is_placeholder")?.extract::<bool>()?,
        total: fixed_decimal_string(&receipt.getattr("total")?)?,
        tax: optional_fixed_decimal_string(&receipt.getattr("tax")?)?,
        image_filename: required_string_attr(receipt, "image_filename")?,
        raw_text: required_string_attr(receipt, "raw_text")?,
        items,
        warnings,
    })
}

#[pyfunction]
fn receipt_format_parsed_receipt(
    receipt: &Bound<'_, PyAny>,
    item_accounts: Vec<String>,
    credit_card_account: String,
    image_sha256: Option<String>,
) -> PyResult<String> {
    let input = extract_formatter_receipt_input(receipt, item_accounts)?;
    Ok(receipt_formatter::format_parsed_receipt(
        &input,
        &credit_card_account,
        image_sha256.as_deref(),
    ))
}

#[pyfunction]
fn receipt_format_draft_beancount(
    receipt: &Bound<'_, PyAny>,
    item_accounts: Vec<String>,
    credit_card_account: String,
) -> PyResult<String> {
    let input = extract_formatter_receipt_input(receipt, item_accounts)?;
    Ok(receipt_formatter::format_draft_beancount(
        &input,
        &credit_card_account,
    ))
}

#[pyfunction]
fn receipt_generate_filename(receipt: &Bound<'_, PyAny>) -> PyResult<String> {
    let date_iso = receipt.getattr("date")?.str()?.extract::<String>()?;
    let date_is_placeholder = receipt.getattr("date_is_placeholder")?.extract::<bool>()?;
    let merchant = required_string_attr(receipt, "merchant")?;
    Ok(receipt_formatter::generate_filename(
        &date_iso,
        date_is_placeholder,
        &merchant,
    ))
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(receipt_format_parsed_receipt, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_format_draft_beancount, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_generate_filename, module)?)?;
    Ok(())
}
