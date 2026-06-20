//! Pure receipt-parsing logic: OCR-detection normalization, line grouping, field /
//! item extraction, categorization, and beancount-text formatting.
//!
//! This crate is deliberately free of any Python (PyO3) or ledger (GPL beancount)
//! dependency so it can be compiled for host platforms and `aarch64-apple-ios`
//! alike. The desktop PyO3 extension (`_rust_matcher`) and the iOS app both depend
//! on this crate; the only output it produces is plain beancount-format text.

pub mod detection_normalization;
pub mod ocr_line_grouping;
pub mod ocr_transform;
pub mod process;
pub mod receipt_categories;
pub mod receipt_common;
pub mod receipt_fields;
pub mod receipt_formatter;
pub mod receipt_parse_helpers;
pub mod receipt_parser;
pub mod rules;
pub mod receipt_spatial;
pub mod receipt_staged_json;
pub mod receipt_text;
pub mod spatial;
