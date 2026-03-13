use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use pyo3::wrap_pyfunction;

use crate::receipt_common;

fn decimalish_to_string(value: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if value.is_none() {
        return Ok(None);
    }
    if let Ok(text) = value.str()?.extract::<String>() {
        let stripped = text.trim().to_string();
        if stripped.is_empty() {
            return Ok(None);
        }
        return Ok(Some(stripped));
    }
    Ok(None)
}

fn decimal_object(py: Python<'_>, value: &str) -> PyResult<Py<PyAny>> {
    let decimal = PyModule::import(py, "decimal")?.getattr("Decimal")?;
    Ok(decimal.call1((value,))?.unbind())
}

fn extract_quantity_modifier(
    modifier: &Bound<'_, PyAny>,
) -> PyResult<receipt_common::QuantityModifier> {
    let dict = modifier
        .cast::<PyDict>()
        .map_err(|_| PyTypeError::new_err("modifier must be a dict"))?;

    let quantity = dict
        .get_item("quantity")?
        .ok_or_else(|| PyTypeError::new_err("modifier.quantity missing"))?
        .extract::<i32>()?;

    let pattern_type = dict
        .get_item("pattern_type")?
        .ok_or_else(|| PyTypeError::new_err("modifier.pattern_type missing"))?
        .extract::<String>()?;

    let raw_line = dict
        .get_item("raw_line")?
        .and_then(|value| value.extract::<String>().ok())
        .unwrap_or_default();

    let unit_price_scaled = dict
        .get_item("unit_price")?
        .map(|value| decimalish_to_string(&value))
        .transpose()?
        .flatten()
        .and_then(|value| receipt_common::parse_scaled_4(&value));

    let deal_price_scaled = dict
        .get_item("deal_price")?
        .map(|value| decimalish_to_string(&value))
        .transpose()?
        .flatten()
        .and_then(|value| receipt_common::parse_scaled_4(&value));

    let weight = dict
        .get_item("weight")?
        .map(|value| decimalish_to_string(&value))
        .transpose()?
        .flatten();

    Ok(receipt_common::QuantityModifier {
        quantity,
        unit_price_scaled,
        weight,
        deal_price_scaled,
        pattern_type,
        raw_line,
    })
}

#[pyfunction]
fn receipt_normalize_decimal_spacing(text: &str) -> String {
    receipt_common::normalize_decimal_spacing(text)
}

#[pyfunction]
fn receipt_is_section_header_text(text: &str) -> bool {
    receipt_common::is_section_header_text(text)
}

#[pyfunction]
fn receipt_strip_leading_receipt_codes(text: &str) -> String {
    receipt_common::strip_leading_receipt_codes(text)
}

#[pyfunction]
fn receipt_looks_like_summary_line(text: &str) -> bool {
    receipt_common::looks_like_summary_line(text)
}

#[pyfunction]
fn receipt_looks_like_receipt_metadata_line(text: &str) -> bool {
    receipt_common::looks_like_receipt_metadata_line(text)
}

#[pyfunction]
fn receipt_line_has_trailing_price(text: &str) -> bool {
    receipt_common::line_has_trailing_price(text)
}

#[pyfunction]
fn receipt_looks_like_onsale_marker(text: &str) -> bool {
    receipt_common::looks_like_onsale_marker(text)
}

#[pyfunction]
fn receipt_is_priced_generic_item_label(left_text: &str, full_text: &str) -> bool {
    receipt_common::is_priced_generic_item_label(left_text, full_text)
}

#[pyfunction]
fn receipt_parse_quantity_modifier(py: Python<'_>, line: &str) -> PyResult<Option<Py<PyDict>>> {
    let Some(modifier) = receipt_common::parse_quantity_modifier(line) else {
        return Ok(None);
    };

    let dict = PyDict::new(py);
    dict.set_item("quantity", modifier.quantity)?;
    dict.set_item(
        "unit_price",
        match modifier.unit_price_scaled {
            Some(value) => Some(decimal_object(py, &receipt_common::format_scaled_4(value))?),
            None => None,
        },
    )?;
    dict.set_item(
        "weight",
        match modifier.weight {
            Some(value) => Some(decimal_object(py, &value)?),
            None => None,
        },
    )?;
    dict.set_item(
        "deal_price",
        match modifier.deal_price_scaled {
            Some(value) => Some(decimal_object(py, &receipt_common::format_scaled_4(value))?),
            None => None,
        },
    )?;
    dict.set_item("pattern_type", modifier.pattern_type)?;
    dict.set_item("raw_line", modifier.raw_line)?;
    Ok(Some(dict.unbind()))
}

#[pyfunction]
#[pyo3(signature = (total_price, modifier, tolerance=None))]
fn receipt_validate_quantity_price(
    total_price: &Bound<'_, PyAny>,
    modifier: &Bound<'_, PyAny>,
    tolerance: Option<&Bound<'_, PyAny>>,
) -> PyResult<bool> {
    let total_price = decimalish_to_string(total_price)?
        .ok_or_else(|| PyTypeError::new_err("total_price is required"))?;
    let tolerance = match tolerance {
        Some(value) => decimalish_to_string(value)?.unwrap_or_else(|| "0.02".to_string()),
        None => "0.02".to_string(),
    };

    let total_modifier = receipt_common::parse_scaled_4(&total_price)
        .ok_or_else(|| PyTypeError::new_err("invalid total_price"))?;
    let tolerance_modifier = receipt_common::parse_scaled_4(&tolerance)
        .ok_or_else(|| PyTypeError::new_err("invalid tolerance"))?;

    Ok(receipt_common::validate_quantity_price(
        total_modifier,
        &extract_quantity_modifier(modifier)?,
        tolerance_modifier,
    ))
}

#[pyfunction]
fn receipt_looks_like_quantity_expression(text: &str) -> bool {
    receipt_common::looks_like_quantity_expression(text)
}

#[pyfunction]
fn receipt_extract_price_word(text: &str) -> Option<String> {
    receipt_common::extract_price_word(text)
}

#[pyfunction]
fn receipt_clean_description(text: &str) -> String {
    receipt_common::clean_description(text)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(receipt_normalize_decimal_spacing, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_is_section_header_text, module)?)?;
    module.add_function(wrap_pyfunction!(
        receipt_strip_leading_receipt_codes,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(receipt_looks_like_summary_line, module)?)?;
    module.add_function(wrap_pyfunction!(
        receipt_looks_like_receipt_metadata_line,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(receipt_line_has_trailing_price, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_looks_like_onsale_marker, module)?)?;
    module.add_function(wrap_pyfunction!(
        receipt_is_priced_generic_item_label,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(receipt_parse_quantity_modifier, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_validate_quantity_price, module)?)?;
    module.add_function(wrap_pyfunction!(
        receipt_looks_like_quantity_expression,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(receipt_extract_price_word, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_clean_description, module)?)?;
    Ok(())
}
