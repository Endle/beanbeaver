use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use pyo3::wrap_pyfunction;

use crate::receipt_parse_helpers;

#[derive(Clone, Debug)]
struct PyMerchantWordInput {
    confidence: f64,
    has_bbox: bool,
}

impl<'a, 'py> FromPyObject<'a, 'py> for PyMerchantWordInput {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        if let Ok(dict) = ob.cast::<PyDict>() {
            let confidence = dict
                .get_item("confidence")?
                .and_then(|value| value.extract::<f64>().ok())
                .unwrap_or(0.0);
            let has_bbox = dict.get_item("bbox")?.is_some();
            return Ok(Self {
                confidence,
                has_bbox,
            });
        }
        Err(PyTypeError::new_err("unsupported word input"))
    }
}

#[derive(FromPyObject)]
struct PyMerchantLineInput {
    #[pyo3(item("text"))]
    text: String,
    #[pyo3(item("words"))]
    words: Vec<PyMerchantWordInput>,
}

#[derive(FromPyObject)]
struct PyMerchantPageInput {
    #[pyo3(item("lines"))]
    lines: Vec<PyMerchantLineInput>,
}

fn to_page_input(page: PyMerchantPageInput) -> receipt_parse_helpers::MerchantPageInput {
    receipt_parse_helpers::MerchantPageInput {
        lines: page
            .lines
            .into_iter()
            .map(|line| receipt_parse_helpers::MerchantLineInput {
                text: line.text,
                words: line
                    .words
                    .into_iter()
                    .map(|word| receipt_parse_helpers::MerchantWordInput {
                        confidence: word.confidence,
                        has_bbox: word.has_bbox,
                    })
                    .collect(),
            })
            .collect(),
    }
}

#[pyfunction]
fn receipt_extract_merchant(
    lines: Vec<String>,
    full_text: &str,
    pages: Vec<PyMerchantPageInput>,
    known_merchants: Option<Vec<String>>,
) -> String {
    receipt_parse_helpers::extract_merchant(
        &lines,
        full_text,
        &pages.into_iter().map(to_page_input).collect::<Vec<_>>(),
        &known_merchants.unwrap_or_default(),
    )
}

#[pyfunction]
fn receipt_has_useful_bbox_data(pages: Vec<PyMerchantPageInput>) -> bool {
    let normalized_pages = pages.into_iter().map(to_page_input).collect::<Vec<_>>();
    receipt_parse_helpers::has_useful_bbox_data(&normalized_pages)
}

#[pyfunction]
fn receipt_is_spatial_layout_receipt(full_text: &str) -> bool {
    receipt_parse_helpers::is_spatial_layout_receipt(full_text)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(receipt_extract_merchant, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_has_useful_bbox_data, module)?)?;
    module.add_function(wrap_pyfunction!(receipt_is_spatial_layout_receipt, module)?)?;
    Ok(())
}
