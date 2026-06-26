use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use pyo3::wrap_pyfunction;

/// Pre-OCR image preprocessing in Rust: decode -> EXIF transpose -> Lanczos
/// resize (cap long side) -> white pad -> JPEG. Drop-in for the Python
/// `resize_image_bytes` default path (EXIF + resize + pad); deskew excluded, as
/// it already is in `default_image_pipeline`.
#[pyfunction]
#[pyo3(signature = (image_bytes, max_dimension = 3000, padding = 50, quality = 95))]
fn preprocess_image_bytes<'py>(
    py: Python<'py>,
    image_bytes: &[u8],
    max_dimension: u32,
    padding: u32,
    quality: u8,
) -> PyResult<Bound<'py, PyBytes>> {
    let out = receipt_image::preprocess_image_bytes(image_bytes, max_dimension, padding, quality)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyBytes::new(py, &out))
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(preprocess_image_bytes, module)?)?;
    Ok(())
}
