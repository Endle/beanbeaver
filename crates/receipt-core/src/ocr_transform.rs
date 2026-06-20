//! Port of `receipt/ocr_helpers.py::transform_paddleocr_result` and the
//! `receipt/detection_normalization.py` pipeline orchestration into pure Rust.
//!
//! Input: raw PaddleOCR detections (padded-image pixel coordinates). Output: the
//! line/word grouping (with normalized [0,1] bboxes) and the joined `full_text`
//! that `receipt_parser::parse_receipt` consumes — split into the helper-page and
//! spatial-page shapes the parser expects.

use crate::detection_normalization::{
    deskew, filter_bob_markers, filter_low_quality, sort_reading_order, Detection,
};
use crate::ocr_line_grouping::group_detections_into_lines;
use crate::receipt_parse_helpers::{MerchantLineInput, MerchantPageInput, MerchantWordInput};
use crate::receipt_spatial::{BboxInput, LineInput, PageInput, WordInput};

/// One raw OCR detection: a polygon (>=2 points, padded-image pixels), the
/// recognized text, and a confidence score.
#[derive(Clone, Debug)]
pub struct RawDetection {
    pub points: Vec<(f64, f64)>,
    pub text: String,
    pub confidence: f64,
}

/// Transformed OCR document, in the two forms the parser needs plus `full_text`.
#[derive(Clone, Debug, Default)]
pub struct TransformedOcr {
    pub full_text: String,
    pub helper_pages: Vec<MerchantPageInput>,
    pub spatial_pages: Vec<PageInput>,
}

fn clamp_unit_interval(value: f64) -> f64 {
    value.max(0.0).min(1.0)
}

/// Apply the default post-OCR pipeline: filter_low_quality -> filter_bob_markers
/// -> deskew -> sort_reading_order. Mirrors `normalize_detections` with
/// `default_detection_pipeline()` (debug-dump I/O omitted — irrelevant on device).
fn normalize(mut dets: Vec<Detection>, image_width: f64) -> Vec<Detection> {
    let keep = filter_low_quality(&dets);
    dets = keep.into_iter().map(|i| dets[i].clone()).collect();

    let keep = filter_bob_markers(&dets);
    dets = keep.into_iter().map(|i| dets[i].clone()).collect();

    let outcome = deskew(&dets, image_width);
    if let Some(new_y) = outcome.new_y {
        for (det, (center_y, y_min, y_max)) in dets.iter_mut().zip(new_y) {
            det.center_y = center_y;
            det.y_min = y_min;
            det.y_max = y_max;
        }
    }

    let order = sort_reading_order(&dets);
    order.into_iter().map(|i| dets[i].clone()).collect()
}

/// Transform raw detections from a padded image into parser inputs.
///
/// `padded_width`/`padded_height` are the OCR-reported (padded) image dims;
/// `padding` is the white border added during pre-OCR resize. Coordinates are
/// de-padded back to original-image space before normalization, exactly as the
/// Python transform does.
pub fn transform(
    detections: Vec<RawDetection>,
    padded_width: i64,
    padded_height: i64,
    padding: i64,
) -> TransformedOcr {
    let image_width = (padded_width - 2 * padding) as f64;
    let image_height = (padded_height - 2 * padding) as f64;

    if detections.is_empty() {
        return TransformedOcr {
            full_text: String::new(),
            helper_pages: vec![MerchantPageInput { lines: Vec::new() }],
            spatial_pages: vec![PageInput { lines: Vec::new() }],
        };
    }

    let pad = padding as f64;
    let mut detection_data: Vec<Detection> = Vec::with_capacity(detections.len());
    for det in detections {
        let adjusted: Vec<(f64, f64)> = det
            .points
            .iter()
            .map(|(x, y)| (x - pad, y - pad))
            .collect();
        let y_coords: Vec<f64> = adjusted.iter().map(|(_, y)| *y).collect();
        let center_y = y_coords.iter().sum::<f64>() / y_coords.len() as f64;
        let y_min = y_coords.iter().cloned().fold(f64::INFINITY, f64::min);
        let y_max = y_coords.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let min_x = adjusted
            .iter()
            .map(|(x, _)| *x)
            .fold(f64::INFINITY, f64::min);
        detection_data.push(Detection {
            confidence: det.confidence,
            text: det.text,
            center_y,
            y_min,
            y_max,
            min_x,
            bbox: adjusted,
        });
    }

    let detection_data = normalize(detection_data, image_width);
    let groups = group_detections_into_lines(&detection_data, image_width);

    let mut full_text_lines: Vec<String> = Vec::with_capacity(groups.len());
    let mut helper_lines: Vec<MerchantLineInput> = Vec::with_capacity(groups.len());
    let mut spatial_lines: Vec<LineInput> = Vec::with_capacity(groups.len());

    for group in groups {
        let mut helper_words = Vec::with_capacity(group.len());
        let mut spatial_words = Vec::with_capacity(group.len());
        let mut texts = Vec::with_capacity(group.len());
        for &idx in &group {
            let det = &detection_data[idx];
            let xs: Vec<f64> = det.bbox.iter().map(|(x, _)| *x).collect();
            let ys: Vec<f64> = det.bbox.iter().map(|(_, y)| *y).collect();
            let bbox = BboxInput {
                left: clamp_unit_interval(xs.iter().cloned().fold(f64::INFINITY, f64::min) / image_width),
                top: clamp_unit_interval(ys.iter().cloned().fold(f64::INFINITY, f64::min) / image_height),
                right: clamp_unit_interval(xs.iter().cloned().fold(f64::NEG_INFINITY, f64::max) / image_width),
                bottom: clamp_unit_interval(ys.iter().cloned().fold(f64::NEG_INFINITY, f64::max) / image_height),
            };
            texts.push(det.text.clone());
            helper_words.push(MerchantWordInput {
                confidence: det.confidence,
                has_bbox: true,
            });
            spatial_words.push(WordInput {
                text: det.text.clone(),
                bbox,
                confidence: det.confidence,
            });
        }
        let line_text = texts.join(" ");
        full_text_lines.push(line_text.clone());
        helper_lines.push(MerchantLineInput {
            text: line_text.clone(),
            words: helper_words,
        });
        spatial_lines.push(LineInput {
            text: line_text,
            words: spatial_words,
        });
    }

    TransformedOcr {
        full_text: full_text_lines.join("\n"),
        helper_pages: vec![MerchantPageInput { lines: helper_lines }],
        spatial_pages: vec![PageInput { lines: spatial_lines }],
    }
}
