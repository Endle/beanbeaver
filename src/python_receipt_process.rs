use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use pyo3::wrap_pyfunction;

use receipt_core::ocr_transform::RawDetection;
use receipt_core::process::process_receipt;

/// Parse the raw PaddleOCR `detections` list (`[[points], [text, conf]]`) into
/// the core's `RawDetection` shape. Points arrive as `[x, y]` lists.
fn extract_detections(detections: &Bound<'_, PyAny>) -> PyResult<Vec<RawDetection>> {
    let list = detections.cast::<PyList>()?;
    let mut out = Vec::with_capacity(list.len());
    for detection in list.try_iter()? {
        let detection = detection?;
        let points_any = detection.get_item(0)?;
        let text_conf = detection.get_item(1)?;
        let points = points_any
            .extract::<Vec<Vec<f64>>>()?
            .into_iter()
            .filter(|p| p.len() >= 2)
            .map(|p| (p[0], p[1]))
            .collect::<Vec<_>>();
        let text = text_conf.get_item(0)?.extract::<String>()?;
        let confidence = text_conf.get_item(1)?.extract::<f64>().unwrap_or(0.0);
        out.push(RawDetection {
            points,
            text,
            confidence,
        });
    }
    Ok(out)
}

/// Run the full on-device pipeline (transform -> parse -> categorize -> format)
/// entirely in `receipt-core`, returning both the structured parse and the
/// rendered beancount. Used by the Phase-1 parity test to compare against the
/// legacy Python chain.
#[pyfunction]
#[pyo3(signature = (
    raw_result,
    *,
    image_filename = String::new(),
    known_merchants = None,
    today,
    credit_card_account = "Liabilities:CreditCard:PENDING".to_string(),
    image_sha256 = None,
    padding = 50,
))]
#[allow(clippy::too_many_arguments)]
fn receipt_process_receipt(
    py: Python<'_>,
    raw_result: &Bound<'_, PyAny>,
    image_filename: String,
    known_merchants: Option<Vec<String>>,
    today: (i32, u32, u32),
    credit_card_account: String,
    image_sha256: Option<String>,
    padding: i64,
) -> PyResult<Py<PyDict>> {
    let dict = raw_result
        .cast::<PyDict>()
        .map_err(|_| PyTypeError::new_err("raw_result must be a dict"))?;
    let padded_width = dict
        .get_item("image_width")?
        .ok_or_else(|| PyTypeError::new_err("raw_result.image_width missing"))?
        .extract::<i64>()?;
    let padded_height = dict
        .get_item("image_height")?
        .ok_or_else(|| PyTypeError::new_err("raw_result.image_height missing"))?
        .extract::<i64>()?;
    let detections = match dict.get_item("detections")? {
        Some(value) => extract_detections(&value)?,
        None => Vec::new(),
    };

    let result = process_receipt(
        detections,
        padded_width,
        padded_height,
        padding,
        &image_filename,
        known_merchants,
        today,
        &credit_card_account,
        image_sha256.as_deref(),
    );
    let parsed = result.parsed;

    let out = PyDict::new(py);
    out.set_item("merchant", parsed.merchant)?;
    out.set_item("date", parsed.date)?;
    out.set_item("date_is_placeholder", parsed.date_is_placeholder)?;
    out.set_item("total", parsed.total)?;
    out.set_item("tax", parsed.tax)?;
    out.set_item("subtotal", parsed.subtotal)?;
    out.set_item(
        "items",
        parsed
            .items
            .into_iter()
            .map(|item| (item.description, item.price, item.quantity, item.category))
            .collect::<Vec<_>>(),
    )?;
    out.set_item(
        "warnings",
        parsed
            .warnings
            .into_iter()
            .map(|warning| (warning.message, warning.after_item_index))
            .collect::<Vec<_>>(),
    )?;
    out.set_item(
        "tenders",
        parsed
            .tenders
            .into_iter()
            .map(|tender| (tender.amount, tender.account, tender.kind, tender.raw_label))
            .collect::<Vec<_>>(),
    )?;
    out.set_item("beancount", result.beancount)?;
    Ok(out.unbind())
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(receipt_process_receipt, module)?)?;
    Ok(())
}
