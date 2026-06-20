use pyo3::prelude::*;
use pyo3::wrap_pyfunction;

use receipt_core::ocr_line_grouping;
use crate::python_detection_normalization::{to_logic, PyDetection};

/// Group detections into reading-order lines. Returns, per line, the source
/// indices into the input list (within-line left-to-right, lines top-to-bottom)
/// so Python keeps the original detection dicts unchanged.
#[pyfunction]
fn group_detections_into_lines(detections: Vec<PyDetection>, image_width: f64) -> Vec<Vec<usize>> {
    ocr_line_grouping::group_detections_into_lines(&to_logic(detections), image_width)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(group_detections_into_lines, module)?)?;
    Ok(())
}
