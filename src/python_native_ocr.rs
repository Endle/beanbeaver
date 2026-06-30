//! Native (no-container) PP-OCRv5 OCR binding.
//!
//! Runs the `ocr-paddle` ONNX pipeline in-process and returns the exact
//! `raw_result` dict shape the desktop `beanbeaver-ocr` container emits, so the
//! existing `transform_paddleocr_result` (+ everything downstream) consumes it
//! unchanged:
//!
//! ```text
//! { "image_width": W, "image_height": H,
//!   "detections": [ [ [[x,y],...], [text, confidence] ], ... ] }   # padded coords
//! ```
//!
//! The caller passes the *already preprocessed* (resized + padded) image bytes —
//! the same `resize_image_bytes` output that was POSTed to the container — so the
//! coordinate space (and `transform`'s padding subtraction) is identical.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use pyo3::wrap_pyfunction;

use ocr_paddle::engine::OcrEngine;

/// One loaded engine, keyed by the models dir it was built from. Loading the
/// det/rec/cls ONNX weights is ~95 MB of work, so keep a single engine alive and
/// rebuild only when the caller points at a different models dir.
static ENGINE: Mutex<Option<(PathBuf, OcrEngine)>> = Mutex::new(None);

/// Find the single `*<suffix>` model file in `dir` (e.g. `_det.onnx`).
fn find_model(dir: &Path, suffix: &str) -> PyResult<PathBuf> {
    let entries = std::fs::read_dir(dir)
        .map_err(|e| PyValueError::new_err(format!("cannot read models dir {}: {e}", dir.display())))?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path
            .file_name()
            .and_then(|n| n.to_str())
            .is_some_and(|n| n.ends_with(suffix))
        {
            return Ok(path);
        }
    }
    Err(PyValueError::new_err(format!(
        "no model file ending in '{suffix}' found in {}",
        dir.display()
    )))
}

/// Run native PP-OCRv5 OCR on preprocessed image bytes; return the container's
/// `raw_result` dict shape. `models_dir` must contain one `*_det.onnx`, one
/// `*_rec.onnx`, and (optionally) one `*_ori.onnx` textline-orientation model.
#[pyfunction]
#[pyo3(signature = (image_bytes, models_dir))]
fn ocr_image_native(py: Python<'_>, image_bytes: &[u8], models_dir: &str) -> PyResult<Py<PyDict>> {
    let img = image::load_from_memory(image_bytes)
        .map_err(|e| PyValueError::new_err(format!("cannot decode image bytes: {e}")))?
        .to_rgb8();
    let (width, height) = (img.width() as i64, img.height() as i64);

    let dir = PathBuf::from(models_dir);
    let mut guard = ENGINE.lock().expect("OCR engine mutex poisoned");
    let needs_build = !matches!(&*guard, Some((cached, _)) if *cached == dir);
    if needs_build {
        let det = find_model(&dir, "_det.onnx")?;
        let rec = find_model(&dir, "_rec.onnx")?;
        let cls = find_model(&dir, "_ori.onnx").ok();
        let engine = OcrEngine::from_paths(&det, &rec, cls.as_ref())
            .map_err(|e| PyRuntimeError::new_err(format!("failed to load OCR models from {}: {e}", dir.display())))?;
        *guard = Some((dir, engine));
    }
    let (_, engine) = guard.as_mut().expect("engine present after build");

    let detections = engine
        .recognize_image(&img)
        .map_err(|e| PyRuntimeError::new_err(format!("OCR inference failed: {e}")))?;

    let out = PyDict::new(py);
    out.set_item("image_width", width)?;
    out.set_item("image_height", height)?;
    let det_list = PyList::empty(py);
    for d in detections {
        let points = PyList::empty(py);
        for p in d.points.iter() {
            points.append(PyList::new(py, [p[0] as f64, p[1] as f64])?)?;
        }
        let text_conf = PyList::empty(py);
        text_conf.append(d.text)?;
        text_conf.append(d.confidence as f64)?;
        let detection = PyList::empty(py);
        detection.append(points)?;
        detection.append(text_conf)?;
        det_list.append(detection)?;
    }
    out.set_item("detections", det_list)?;
    Ok(out.unbind())
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(ocr_image_native, module)?)?;
    Ok(())
}
