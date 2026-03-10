use pyo3::prelude::*;
use pyo3::wrap_pyfunction;
use std::collections::HashSet;

use crate::receipt_text;

#[pyfunction]
fn receipt_extract_text_items(
    lines: Vec<String>,
    summary_amounts_cents: Vec<i64>,
) -> (
    Vec<(String, String, i64, i32)>,
    Vec<(String, Option<usize>)>,
) {
    let summary_amounts = summary_amounts_cents.into_iter().collect::<HashSet<_>>();
    let (items, warnings) = receipt_text::extract_text_items(&lines, &summary_amounts);
    (
        items.into_iter()
            .map(|item| (item.description, item.category_source, item.price_cents, item.quantity))
            .collect(),
        warnings
            .into_iter()
            .map(|warning| (warning.message, warning.after_item_index))
            .collect(),
    )
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(receipt_extract_text_items, module)?)?;
    Ok(())
}
