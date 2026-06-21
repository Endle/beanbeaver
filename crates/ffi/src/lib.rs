//! UniFFI seam between Swift and the Rust receipt core.
//!
//! Swift loads the OCR models once (`OcrSession::new`) and then calls
//! `scan` per captured image, handing in encoded JPEG/PNG bytes and getting
//! back a structured receipt + beancount fragment. Everything heavy (ONNX
//! inference, OCR post-processing, parsing, categorizing, formatting) runs
//! here in Rust — the "fat-Rust" seam from `docs/ios_port.md`.

use std::fmt;
use std::sync::{Arc, Mutex};

use ocr_paddle::engine::OcrEngine;
use ocr_paddle::process::process_image;
use receipt_core::process::ProcessedReceipt;

uniffi::setup_scaffolding!();

/// Fixed bundle filenames for the three converted PP-OCRv5 models. The Swift
/// app ships these as resources; `OcrSession::new` is handed their directory.
const DET_MODEL: &str = "PP-OCRv5_mobile_det.onnx";
const REC_MODEL: &str = "PP-OCRv5_mobile_rec.onnx";
const CLS_MODEL: &str = "PP-LCNet_x1_0_textline_ori.onnx";

/// Calendar date passed in from Swift (used for date inference + placeholder).
#[derive(uniffi::Record)]
pub struct DateYmd {
    pub year: i32,
    pub month: u32,
    pub day: u32,
}

/// One parsed line item.
#[derive(uniffi::Record)]
pub struct ReceiptItem {
    pub description: String,
    pub price: String,
    pub quantity: i32,
    pub category: Option<String>,
}

/// Flattened, Swift-friendly view of `ProcessedReceipt`.
#[derive(uniffi::Record)]
pub struct ReceiptResult {
    pub merchant: String,
    /// ISO `YYYY-MM-DD`, or `None` if the parser found no date.
    pub date: Option<String>,
    pub date_is_placeholder: bool,
    pub total: String,
    pub tax: Option<String>,
    pub subtotal: Option<String>,
    pub items: Vec<ReceiptItem>,
    pub warnings: Vec<String>,
    pub beancount: String,
}

/// Errors surfaced to Swift as a typed exception.
#[derive(Debug, uniffi::Error)]
pub enum ScanError {
    ModelLoad { msg: String },
    ImageDecode { msg: String },
    Inference { msg: String },
}

impl fmt::Display for ScanError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ScanError::ModelLoad { msg } => write!(f, "failed to load OCR models: {msg}"),
            ScanError::ImageDecode { msg } => write!(f, "failed to decode image: {msg}"),
            ScanError::Inference { msg } => write!(f, "OCR/parse failed: {msg}"),
        }
    }
}

impl std::error::Error for ScanError {}

/// A loaded OCR pipeline. Construct once, reuse across scans. The engine needs
/// `&mut` for inference, so it's wrapped in a `Mutex` (UniFFI objects are shared
/// `Arc`s); scans on one session are therefore serialized.
#[derive(uniffi::Object)]
pub struct OcrSession {
    engine: Mutex<OcrEngine>,
}

#[uniffi::export]
impl OcrSession {
    /// Load the three PP-OCRv5 models from `model_dir` (the bundle directory
    /// holding `PP-OCRv5_mobile_det.onnx`, `_rec.onnx`, `PP-LCNet…_ori.onnx`).
    #[uniffi::constructor]
    pub fn new(model_dir: String) -> Result<Arc<Self>, ScanError> {
        let dir = std::path::Path::new(&model_dir);
        let engine = OcrEngine::from_paths(
            dir.join(DET_MODEL),
            dir.join(REC_MODEL),
            Some(dir.join(CLS_MODEL)),
        )
        .map_err(|e| ScanError::ModelLoad { msg: e.to_string() })?;
        Ok(Arc::new(Self {
            engine: Mutex::new(engine),
        }))
    }

    /// Run the full image → beancount pipeline on encoded image bytes.
    pub fn scan(
        &self,
        image_bytes: Vec<u8>,
        today: DateYmd,
        credit_card_account: String,
    ) -> Result<ReceiptResult, ScanError> {
        let img = image::load_from_memory(&image_bytes)
            .map_err(|e| ScanError::ImageDecode { msg: e.to_string() })?
            .to_rgb8();

        let mut engine = self
            .engine
            .lock()
            .map_err(|e| ScanError::Inference { msg: format!("engine lock poisoned: {e}") })?;

        let processed = process_image(
            &mut engine,
            &img,
            "receipt.jpg",
            (today.year, today.month, today.day),
            &credit_card_account,
            None,
        )
        .map_err(|e| ScanError::Inference { msg: e.to_string() })?;

        Ok(to_result(processed))
    }
}

/// Flatten the rich `ProcessedReceipt` into the FFI record.
fn to_result(p: ProcessedReceipt) -> ReceiptResult {
    let d = p.parsed;
    ReceiptResult {
        merchant: d.merchant,
        date: d.date.map(|(y, m, day)| format!("{y:04}-{m:02}-{day:02}")),
        date_is_placeholder: d.date_is_placeholder,
        total: d.total,
        tax: d.tax,
        subtotal: d.subtotal,
        items: d
            .items
            .into_iter()
            .map(|i| ReceiptItem {
                description: i.description,
                price: i.price,
                quantity: i.quantity,
                category: i.category,
            })
            .collect(),
        warnings: d.warnings.into_iter().map(|w| w.message).collect(),
        beancount: p.beancount,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Full FFI round-trip on the committed fixture. Mirrors ocr-paddle's
    // end-to-end test but exercises the Swift-facing entry points.
    //   cargo test -p bb-receipt-ffi -- --ignored --nocapture
    #[test]
    #[ignore = "needs converted models + fixture"]
    fn scan_costco_fixture_end_to_end() {
        let session = OcrSession::new("../../models".to_string()).expect("load models");
        let bytes = std::fs::read("../../tests/receipts_e2e/costco_20260218_redact.jpg")
            .expect("read fixture");

        let r = session
            .scan(
                bytes,
                DateYmd { year: 2026, month: 2, day: 18 },
                "Liabilities:CreditCard".to_string(),
            )
            .expect("scan");

        assert_eq!(r.merchant, "COSTCO");
        assert_eq!(r.date.as_deref(), Some("2026-02-18"));
        assert_eq!(r.total, "221.97");
        assert_eq!(r.tax.as_deref(), Some("4.44"));
        assert!(!r.items.is_empty());
        assert!(r.beancount.contains("COSTCO"));
        println!("{}", r.beancount);
    }
}
