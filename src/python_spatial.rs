use pyo3::prelude::*;
use pyo3::wrap_pyfunction;

use crate::spatial;

#[derive(FromPyObject)]
struct PySpatialLineCandidateInput {
    #[pyo3(item("line_y"))]
    line_y: f64,
    #[pyo3(item("is_used"))]
    is_used: bool,
    #[pyo3(item("is_valid_item_line"))]
    is_valid_item_line: bool,
    #[pyo3(item("has_trailing_price"))]
    has_trailing_price: bool,
    #[pyo3(item("looks_like_quantity_expression"))]
    looks_like_quantity_expression: bool,
}

fn to_spatial_line_candidate(
    candidate: PySpatialLineCandidateInput,
) -> spatial::SpatialLineCandidate {
    spatial::SpatialLineCandidate::new(
        candidate.line_y,
        candidate.is_used,
        candidate.is_valid_item_line,
        candidate.has_trailing_price,
        candidate.looks_like_quantity_expression,
    )
}

#[pyfunction]
fn select_spatial_item_line(
    price_y: f64,
    y_tolerance: f64,
    max_item_distance: f64,
    prefer_below: bool,
    price_line_has_onsale: bool,
    candidates: Vec<PySpatialLineCandidateInput>,
) -> Option<(usize, f64)> {
    spatial::select_spatial_item_line(
        price_y,
        y_tolerance,
        max_item_distance,
        prefer_below,
        price_line_has_onsale,
        candidates
            .into_iter()
            .map(to_spatial_line_candidate)
            .collect(),
    )
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(select_spatial_item_line, module)?)?;
    Ok(())
}
