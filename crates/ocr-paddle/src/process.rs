//! The fat-Rust seam's single entry: a decoded image -> structured receipt +
//! beancount, fully on-device. Mirrors the desktop flow
//! (`resize_image_bytes` -> OCR service -> `process_receipt`).

use image::{Rgb, RgbImage};
use receipt_core::ocr_transform::RawDetection;
use receipt_core::process::{process_receipt, ProcessedReceipt};

use crate::engine::OcrEngine;

/// Matches `image_pipeline.MAX_IMAGE_DIMENSION` / `OCR_IMAGE_PADDING`.
pub const MAX_IMAGE_DIMENSION: u32 = 3000;
pub const OCR_IMAGE_PADDING: u32 = 50;

/// Pre-OCR image prep, matching the desktop `resize_image_bytes`: cap the longer
/// side at 3000 (Lanczos), then pad a 50px white border. (EXIF orientation is
/// handled upstream by the capture layer.)
pub fn resize_and_pad(img: &RgbImage) -> RgbImage {
    let (w, h) = (img.width(), img.height());
    let longer = w.max(h);
    let resized = if longer > MAX_IMAGE_DIMENSION {
        let r = MAX_IMAGE_DIMENSION as f32 / longer as f32;
        let (nw, nh) = ((w as f32 * r).round() as u32, (h as f32 * r).round() as u32);
        image::imageops::resize(img, nw.max(1), nh.max(1), image::imageops::FilterType::Lanczos3)
    } else {
        img.clone()
    };

    let pad = OCR_IMAGE_PADDING;
    let mut padded = RgbImage::from_pixel(resized.width() + 2 * pad, resized.height() + 2 * pad, Rgb([255, 255, 255]));
    image::imageops::overlay(&mut padded, &resized, pad as i64, pad as i64);
    padded
}

/// Run the whole pipeline: image -> OCR -> parse/categorize/format.
///
/// `today` is `(year, month, day)` for date inference + the placeholder date.
#[allow(clippy::too_many_arguments)]
pub fn process_image(
    engine: &mut OcrEngine,
    img: &RgbImage,
    image_filename: &str,
    today: (i32, u32, u32),
    credit_card_account: &str,
    image_sha256: Option<&str>,
) -> ort::Result<ProcessedReceipt> {
    let prepared = resize_and_pad(img);
    let detections = engine.recognize_image(&prepared)?;

    let raw: Vec<RawDetection> = detections
        .into_iter()
        .map(|d| RawDetection {
            points: d.points.iter().map(|p| (p[0] as f64, p[1] as f64)).collect(),
            text: d.text,
            confidence: d.confidence as f64,
        })
        .collect();

    Ok(process_receipt(
        raw,
        prepared.width() as i64,
        prepared.height() as i64,
        OCR_IMAGE_PADDING as i64,
        image_filename,
        None, // bundled default known-merchants
        today,
        credit_card_account,
        image_sha256,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::OcrEngine;

    // Whole pipeline (image -> beancount), on-device-equivalent. Run with:
    //   cargo test -p ocr-paddle -- --ignored --nocapture
    #[test]
    #[ignore = "needs converted models + fixture"]
    fn process_image_end_to_end_costco() {
        let img = image::open("../../tests/receipts_e2e/costco_20260218_redact.jpg")
            .expect("load fixture")
            .to_rgb8();
        let mut engine = OcrEngine::from_paths(
            "../../models/PP-OCRv5_mobile_det.onnx",
            "../../models/PP-OCRv5_mobile_rec.onnx",
            Some("../../models/PP-LCNet_x1_0_textline_ori.onnx"),
        )
        .unwrap();

        let result = process_image(
            &mut engine,
            &img,
            "costco_20260218_redact",
            (2026, 2, 18),
            "Liabilities:CreditCard:PENDING",
            None,
        )
        .unwrap();

        let p = &result.parsed;
        eprintln!(
            "merchant={} date={:?} total={} tax={:?} subtotal={:?} items={}",
            p.merchant, p.date, p.total, p.tax, p.subtotal, p.items.len()
        );
        for it in &p.items {
            eprintln!("  {:>8}  {}  [{:?}]", it.price, it.description, it.category);
        }
        eprintln!("\n--- beancount ---\n{}", result.beancount);

        assert!(p.merchant.to_uppercase().contains("COSTCO"), "merchant: {}", p.merchant);
        assert_eq!(p.total, "221.97");
    }
}
