use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use pyo3::wrap_pyfunction;

use receipt_core::detection_normalization as logic;

/// Numeric view of a Python detection dict. Missing numeric fields default to
/// 0.0 and a missing/blank text to the empty string so the noop and partial
/// fixtures used by the test suite extract without error.
pub(crate) struct PyDetection(pub(crate) logic::Detection);

fn get_f64(dict: &Bound<'_, PyDict>, key: &str, default: f64) -> PyResult<f64> {
    Ok(match dict.get_item(key)? {
        Some(value) => value.extract::<f64>().unwrap_or(default),
        None => default,
    })
}

impl<'a, 'py> FromPyObject<'a, 'py> for PyDetection {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        let dict = ob
            .cast::<PyDict>()
            .map_err(|_| PyTypeError::new_err("detection must be a dict"))?;
        let text = match dict.get_item("text")? {
            Some(value) => value.extract::<String>().unwrap_or_default(),
            None => String::new(),
        };
        // bbox points arrive as lists (`[x, y]`) in production and in tests;
        // extracting through `Vec<Vec<f64>>` accepts both lists and tuples,
        // unlike `Vec<(f64, f64)>` which only matches Python tuples.
        let bbox = match dict.get_item("bbox")? {
            Some(value) => value
                .extract::<Vec<Vec<f64>>>()
                .unwrap_or_default()
                .into_iter()
                .filter(|point| point.len() >= 2)
                .map(|point| (point[0], point[1]))
                .collect(),
            None => Vec::new(),
        };
        Ok(PyDetection(logic::Detection {
            confidence: get_f64(&dict, "confidence", 0.0)?,
            text,
            center_y: get_f64(&dict, "center_y", 0.0)?,
            y_min: get_f64(&dict, "y_min", 0.0)?,
            y_max: get_f64(&dict, "y_max", 0.0)?,
            min_x: get_f64(&dict, "min_x", 0.0)?,
            bbox,
        }))
    }
}

pub(crate) fn to_logic(detections: Vec<PyDetection>) -> Vec<logic::Detection> {
    detections.into_iter().map(|det| det.0).collect()
}

#[pyfunction]
fn detection_filter_low_quality(detections: Vec<PyDetection>) -> Vec<usize> {
    logic::filter_low_quality(&to_logic(detections))
}

#[pyfunction]
fn detection_filter_bob_markers(detections: Vec<PyDetection>) -> Vec<usize> {
    logic::filter_bob_markers(&to_logic(detections))
}

#[pyfunction]
fn detection_sort_reading_order(detections: Vec<PyDetection>) -> Vec<usize> {
    logic::sort_reading_order(&to_logic(detections))
}

/// Runs the deskew pass. Returns `(trace_record, new_y)` where `new_y` is the
/// per-detection `(center_y, y_min, y_max)` triples to apply, or `None` when
/// the pass is gated out and detections should pass through unchanged.
#[pyfunction]
fn detection_deskew(
    py: Python<'_>,
    detections: Vec<PyDetection>,
    image_width: f64,
) -> PyResult<(Py<PyDict>, Option<Vec<(f64, f64, f64)>>)> {
    let outcome = logic::deskew(&to_logic(detections), image_width);
    let record = PyDict::new(py);
    record.set_item("op", "deskew_detections")?;
    record.set_item("angle_deg", outcome.angle_deg)?;
    record.set_item("applied", outcome.applied)?;
    record.set_item("candidate_count", outcome.candidate_count)?;
    record.set_item("inlier_count", outcome.inlier_count)?;
    record.set_item("consensus_ratio", outcome.consensus_ratio)?;
    record.set_item("gate_reason", outcome.gate_reason)?;
    Ok((record.unbind(), outcome.new_y))
}

/// Single source of truth for the pipeline thresholds. The Python module binds
/// these to module-level names that tests and downstream code import.
#[pyfunction]
fn detection_constants(py: Python<'_>) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("MIN_CONFIDENCE", logic::MIN_CONFIDENCE)?;
    dict.set_item("MIN_TEXT_LENGTH", logic::MIN_TEXT_LENGTH)?;
    dict.set_item("DESKEW_MIN_CONFIDENCE", logic::DESKEW_MIN_CONFIDENCE)?;
    dict.set_item("DESKEW_MIN_ITEM_WIDTH", logic::DESKEW_MIN_ITEM_WIDTH)?;
    dict.set_item("DESKEW_MIN_PRICE_WIDTH", logic::DESKEW_MIN_PRICE_WIDTH)?;
    dict.set_item("DESKEW_MIN_X_DISTANCE", logic::DESKEW_MIN_X_DISTANCE)?;
    dict.set_item("DESKEW_ITEM_X_MAX_FRAC", logic::DESKEW_ITEM_X_MAX_FRAC)?;
    dict.set_item("DESKEW_PRICE_X_MIN_FRAC", logic::DESKEW_PRICE_X_MIN_FRAC)?;
    dict.set_item("DESKEW_Y_WINDOW_PX", logic::DESKEW_Y_WINDOW_PX as i64)?;
    dict.set_item("DESKEW_ANGLE_CAP_DEG", logic::DESKEW_ANGLE_CAP_DEG)?;
    dict.set_item("DESKEW_MIN_ANGLE_DEG", logic::DESKEW_MIN_ANGLE_DEG)?;
    dict.set_item("DESKEW_INLIER_TOL_DEG", logic::DESKEW_INLIER_TOL_DEG)?;
    dict.set_item("DESKEW_MIN_INLIERS", logic::DESKEW_MIN_INLIERS)?;
    dict.set_item("DESKEW_MIN_CONSENSUS", logic::DESKEW_MIN_CONSENSUS)?;
    dict.set_item("DESKEW_RANSAC_ITERS", logic::DESKEW_RANSAC_ITERS)?;
    dict.set_item("DESKEW_RANSAC_SEED", logic::DESKEW_RANSAC_SEED)?;
    Ok(dict.unbind())
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(detection_filter_low_quality, module)?)?;
    module.add_function(wrap_pyfunction!(detection_filter_bob_markers, module)?)?;
    module.add_function(wrap_pyfunction!(detection_sort_reading_order, module)?)?;
    module.add_function(wrap_pyfunction!(detection_deskew, module)?)?;
    module.add_function(wrap_pyfunction!(detection_constants, module)?)?;
    Ok(())
}
