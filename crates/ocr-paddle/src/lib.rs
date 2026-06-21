//! On-device PP-OCRv5 OCR pipeline (the "fat Rust" seam): image pixels ->
//! detections (`[bbox, [text, conf]]`), matching the desktop `beanbeaver-ocr`
//! service so downstream `receipt-core` parsing behaves the same.
//!
//! Stages (built incrementally):
//! - [`preprocess`] — detection input tensor (resize_long 960 / pad-32 / normalize).
//! - `db_postprocess` — DB probability map -> quad boxes (next).
//! - recognition + CTC decode, textline-orientation cls, and `ort` inference wiring.

pub mod classify;
pub mod db_postprocess;
pub mod detect;
pub mod engine;
pub mod preprocess;
pub mod process;
pub mod recognize;
