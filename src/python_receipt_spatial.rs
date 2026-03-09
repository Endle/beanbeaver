use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use pyo3::wrap_pyfunction;

use crate::receipt_spatial;

#[derive(Clone, Debug)]
struct PyBboxInput {
    left: f64,
    top: f64,
    right: f64,
    bottom: f64,
}

impl<'a, 'py> FromPyObject<'a, 'py> for PyBboxInput {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        if let Ok(dict) = ob.cast::<PyDict>() {
            let left = dict
                .get_item("left")?
                .ok_or_else(|| PyTypeError::new_err("bbox.left missing"))?
                .extract::<f64>()?;
            let top = dict
                .get_item("top")?
                .ok_or_else(|| PyTypeError::new_err("bbox.top missing"))?
                .extract::<f64>()?;
            let right = dict
                .get_item("right")?
                .ok_or_else(|| PyTypeError::new_err("bbox.right missing"))?
                .extract::<f64>()?;
            let bottom = dict
                .get_item("bottom")?
                .ok_or_else(|| PyTypeError::new_err("bbox.bottom missing"))?
                .extract::<f64>()?;
            return Ok(Self {
                left,
                top,
                right,
                bottom,
            });
        }

        if let Ok(points) = ob.extract::<Vec<(f64, f64)>>() {
            if points.len() >= 2 {
                return Ok(Self {
                    left: points[0].0,
                    top: points[0].1,
                    right: points[1].0,
                    bottom: points[1].1,
                });
            }
        }

        Err(PyTypeError::new_err("unsupported bbox shape"))
    }
}

#[derive(FromPyObject)]
struct PyWordInput {
    #[pyo3(item("text"))]
    text: String,
    #[pyo3(item("bbox"))]
    bbox: PyBboxInput,
    #[pyo3(item("confidence"))]
    confidence: Option<f64>,
}

#[derive(FromPyObject)]
struct PyLineInput {
    #[pyo3(item("text"))]
    text: String,
    #[pyo3(item("words"))]
    words: Vec<PyWordInput>,
}

#[derive(FromPyObject)]
struct PyPageInput {
    #[pyo3(item("lines"))]
    lines: Vec<PyLineInput>,
}

fn to_page_input(page: PyPageInput) -> receipt_spatial::PageInput {
    receipt_spatial::PageInput {
        lines: page
            .lines
            .into_iter()
            .map(|line| receipt_spatial::LineInput {
                text: line.text,
                words: line
                    .words
                    .into_iter()
                    .map(|word| receipt_spatial::WordInput {
                        text: word.text,
                        bbox: receipt_spatial::BboxInput {
                            left: word.bbox.left,
                            top: word.bbox.top,
                            right: word.bbox.right,
                            bottom: word.bbox.bottom,
                        },
                        confidence: word.confidence.unwrap_or(0.0),
                    })
                    .collect(),
            })
            .collect(),
    }
}

#[pyfunction]
fn extract_spatial_items(
    pages: Vec<PyPageInput>,
) -> (Vec<(String, i64)>, Vec<(String, Option<usize>)>) {
    let outcome =
        receipt_spatial::extract_spatial_items(pages.into_iter().map(to_page_input).collect());
    (
        outcome
            .items
            .into_iter()
            .map(|item| (item.description, item.price_scaled))
            .collect(),
        outcome
            .warnings
            .into_iter()
            .map(|warning| (warning.message, warning.after_item_index))
            .collect(),
    )
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(extract_spatial_items, module)?)?;
    Ok(())
}
