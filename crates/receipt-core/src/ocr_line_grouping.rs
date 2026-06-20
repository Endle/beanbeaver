//! Group post-OCR detections into reading-order lines.
//!
//! Item-first matching that mirrors the receipt layout: SKU/summary tokens on
//! the left are paired with the first vertically-overlapping price on the
//! right, then middle-column descriptions attach to the nearest line. Pure
//! geometry; the Python wrapper marshals detection dicts and builds the OCR
//! schema. Operates on the shared [`Detection`] view and returns groups of
//! source indices so the caller keeps the original dicts intact.

use std::cmp::Ordering;
use std::sync::OnceLock;

use regex::Regex;

use crate::detection_normalization::{boxes_overlap_y, Detection};

fn summary_label_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)^\s*(?:SUB\s*T[OCQDG0]TAL|SUBTOTAL|TOTAL|HST|GST|PST|TAX|MASTER(?:CARD)?|VISA|DEBIT|CREDIT|POINTS|CASH|CHANGE|BALANCE|APPROVED|CARD|TERMINAL|MEMBER|AMOUNT|REFERENCE|AUTH)\b",
        )
        .unwrap()
    })
}

/// Decide whether a detection sits in the LEFT (SKU/summary-label) column.
///
/// `x_norm < 0.2` is unambiguously LEFT. In the 0.2-0.3 transition band the
/// answer depends on content: numeric SKU-style tokens (digit-led) and summary
/// labels (TOTAL, TAX, …) belong on the LEFT; alpha-led short tokens like
/// Costco's `CRAISINS 1.8` are descriptions and belong in MIDDLE.
fn belongs_in_left_column(text: &str, x_norm: f64) -> bool {
    if x_norm < 0.2 {
        return true;
    }
    if x_norm >= 0.3 {
        return false;
    }
    let stripped = text.trim_start();
    let Some(first) = stripped.chars().next() else {
        return false;
    };
    if first.is_ascii_digit() {
        return true;
    }
    summary_label_re().is_match(stripped)
}

/// Adaptive Y-threshold for middle-column line merges. Larger text/blur ->
/// larger tolerance, clamped to avoid cross-row merges.
fn adaptive_middle_y_threshold(dets: &[Detection]) -> f64 {
    let mut heights: Vec<f64> = dets
        .iter()
        .map(|det| det.y_max - det.y_min)
        .filter(|height| *height > 0.0)
        .collect();
    if heights.is_empty() {
        return 24.0;
    }
    heights.sort_by(|a, b| a.partial_cmp(b).unwrap_or(Ordering::Equal));
    let median_height = heights[heights.len() / 2];
    (median_height * 0.8).clamp(12.0, 30.0)
}

fn line_y_span(dets: &[Detection], line: &[usize]) -> (f64, f64) {
    let mut min_y = f64::INFINITY;
    let mut max_y = f64::NEG_INFINITY;
    for &index in line {
        min_y = min_y.min(dets[index].y_min);
        max_y = max_y.max(dets[index].y_max);
    }
    (min_y, max_y)
}

fn line_center_y(dets: &[Detection], line: &[usize]) -> f64 {
    let sum: f64 = line.iter().map(|&index| dets[index].center_y).sum();
    sum / line.len() as f64
}

fn line_overlap_ratio(dets: &[Detection], det_index: usize, line: &[usize]) -> f64 {
    let det = &dets[det_index];
    let (line_min, line_max) = line_y_span(dets, line);
    let overlap_start = det.y_min.max(line_min);
    let overlap_end = det.y_max.min(line_max);
    if overlap_start >= overlap_end {
        return 0.0;
    }
    let overlap = overlap_end - overlap_start;
    let det_height = (det.y_max - det.y_min).max(1e-6);
    let line_height = (line_max - line_min).max(1e-6);
    overlap / det_height.min(line_height)
}

fn distance_to_line_span(dets: &[Detection], det_index: usize, line: &[usize]) -> f64 {
    let center_y = dets[det_index].center_y;
    let (line_min, line_max) = line_y_span(dets, line);
    if line_min <= center_y && center_y <= line_max {
        0.0
    } else if center_y < line_min {
        line_min - center_y
    } else {
        center_y - line_max
    }
}

/// Stable sort of `indices` by each detection's `center_y`.
fn sort_by_center_y(dets: &[Detection], indices: &mut [usize]) {
    indices.sort_by(|&a, &b| {
        dets[a]
            .center_y
            .partial_cmp(&dets[b].center_y)
            .unwrap_or(Ordering::Equal)
    });
}

/// Lexicographic comparison of the middle-column placement score
/// `(overlap_rank, distance_to_span, center_distance)`.
fn score_less(a: (u8, f64, f64), b: (u8, f64, f64)) -> bool {
    match a.0.cmp(&b.0) {
        Ordering::Less => true,
        Ordering::Greater => false,
        Ordering::Equal => match a.1.partial_cmp(&b.1).unwrap_or(Ordering::Equal) {
            Ordering::Less => true,
            Ordering::Greater => false,
            Ordering::Equal => a.2 < b.2,
        },
    }
}

/// Group detections into lines using item-first matching. Each returned line is
/// a list of source indices: within a line sorted left-to-right by `min_x`, and
/// lines ordered top-to-bottom by average `center_y`.
pub fn group_detections_into_lines(dets: &[Detection], image_width: f64) -> Vec<Vec<usize>> {
    if dets.is_empty() {
        return Vec::new();
    }

    // Partition into LEFT / MIDDLE / RIGHT, preserving detection order so the
    // subsequent stable center_y sorts match the Python list semantics.
    let mut left: Vec<usize> = Vec::new();
    let mut middle: Vec<usize> = Vec::new();
    let mut right: Vec<usize> = Vec::new();
    for (index, det) in dets.iter().enumerate() {
        let x_norm = det.min_x / image_width;
        if x_norm > 0.7 {
            right.push(index);
        } else if belongs_in_left_column(&det.text, x_norm) {
            left.push(index);
        } else {
            middle.push(index);
        }
    }

    sort_by_center_y(dets, &mut left);
    sort_by_center_y(dets, &mut right);

    let mut assigned_prices = vec![false; right.len()];
    let mut lines: Vec<Vec<usize>> = Vec::new();

    // Each LEFT item claims the first unassigned RIGHT price that overlaps it.
    for &left_index in &left {
        let mut matched: Option<usize> = None;
        for (slot, &right_index) in right.iter().enumerate() {
            if assigned_prices[slot] {
                continue;
            }
            if boxes_overlap_y(&dets[left_index], &dets[right_index], 0.3) {
                matched = Some(slot);
                break;
            }
        }
        match matched {
            Some(slot) => {
                lines.push(vec![left_index, right[slot]]);
                assigned_prices[slot] = true;
            }
            None => lines.push(vec![left_index]),
        }
    }

    // Orphan prices stand as their own lines.
    for (slot, &right_index) in right.iter().enumerate() {
        if !assigned_prices[slot] {
            lines.push(vec![right_index]);
        }
    }

    // MIDDLE descriptions attach to the best-aligned existing line.
    let y_threshold = adaptive_middle_y_threshold(dets);
    let overlap_threshold = 0.25;
    for &mid_index in &middle {
        let mut best_line: Option<usize> = None;
        let mut best_score: Option<(u8, f64, f64)> = None;
        for line_idx in 0..lines.len() {
            let overlap_ratio = line_overlap_ratio(dets, mid_index, &lines[line_idx]);
            let center_distance =
                (dets[mid_index].center_y - line_center_y(dets, &lines[line_idx])).abs();
            if overlap_ratio < overlap_threshold && center_distance > y_threshold {
                continue;
            }
            let score = (
                if overlap_ratio >= overlap_threshold { 0 } else { 1 },
                distance_to_line_span(dets, mid_index, &lines[line_idx]),
                center_distance,
            );
            if best_score.is_none() || score_less(score, best_score.unwrap()) {
                best_score = Some(score);
                best_line = Some(line_idx);
            }
        }
        match best_line {
            Some(line_idx) => lines[line_idx].push(mid_index),
            None => lines.push(vec![mid_index]),
        }
    }

    // Within-line left-to-right, then lines top-to-bottom (both stable).
    for line in &mut lines {
        line.sort_by(|&a, &b| {
            dets[a]
                .min_x
                .partial_cmp(&dets[b].min_x)
                .unwrap_or(Ordering::Equal)
        });
    }
    lines.sort_by(|a, b| {
        line_center_y(dets, a)
            .partial_cmp(&line_center_y(dets, b))
            .unwrap_or(Ordering::Equal)
    });

    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    fn det(text: &str, min_x: f64, cy: f64) -> Detection {
        Detection {
            confidence: 0.99,
            text: text.to_string(),
            center_y: cy,
            y_min: cy - 20.0,
            y_max: cy + 20.0,
            min_x,
            bbox: Vec::new(),
        }
    }

    #[test]
    fn left_column_routing() {
        assert!(belongs_in_left_column("anything", 0.1));
        assert!(!belongs_in_left_column("anything", 0.35));
        assert!(belongs_in_left_column("232952 COKE", 0.25)); // digit-led SKU
        assert!(belongs_in_left_column("TOTAL", 0.25)); // summary label
        assert!(!belongs_in_left_column("CRAISINS 1.8", 0.25)); // alpha description
    }

    #[test]
    fn pairs_left_item_with_overlapping_right_price() {
        let dets = vec![
            det("232952 COKE", 120.0, 220.0), // left
            det("17.19", 760.0, 220.0),       // right, same row
            det("305882 IBU", 120.0, 340.0),  // left
            det("16.99", 760.0, 340.0),       // right, same row
        ];
        let lines = group_detections_into_lines(&dets, 1000.0);
        assert_eq!(lines.len(), 2);
        // top row first, item before price
        assert_eq!(lines[0], vec![0, 1]);
        assert_eq!(lines[1], vec![2, 3]);
    }

    #[test]
    fn orphan_price_becomes_its_own_line() {
        let dets = vec![det("MILK", 120.0, 220.0), det("4.99", 760.0, 900.0)];
        let lines = group_detections_into_lines(&dets, 1000.0);
        assert_eq!(lines.len(), 2);
    }

    #[test]
    fn empty_input() {
        assert!(group_detections_into_lines(&[], 1000.0).is_empty());
    }
}
