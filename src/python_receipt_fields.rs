use pyo3::prelude::*;
use pyo3::wrap_pyfunction;

use crate::receipt_fields;

#[pyfunction]
fn receipt_extract_price_from_line(line: &str) -> Option<i64> {
    receipt_fields::extract_price_from_line(line)
}

#[pyfunction]
fn receipt_extract_total(lines: Vec<String>) -> i64 {
    receipt_fields::extract_total(&lines)
}

#[pyfunction]
fn receipt_extract_tax(lines: Vec<String>) -> Option<i64> {
    receipt_fields::extract_tax(&lines)
}

#[pyfunction]
fn receipt_extract_subtotal(lines: Vec<String>) -> Option<i64> {
    receipt_fields::extract_subtotal(&lines)
}

#[pyfunction]
fn receipt_extract_date(
    lines: Vec<String>,
    full_text: &str,
    current_year: i32,
) -> Option<(i32, u32, u32)> {
    receipt_fields::extract_date(&lines, full_text, current_year)
        .map(|date| (date.year, date.month, date.day))
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(receipt_extract_price_from_line, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_extract_total, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_extract_tax, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_extract_subtotal, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_extract_date, module)?)?;
    Ok(())
}
