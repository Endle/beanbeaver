//! Post-OCR detection normalization logic.
//!
//! Pure `(detections) -> instructions` passes mirroring the Python pipeline at
//! the bbox layer. The Python wrapper owns orchestration, dict marshaling, and
//! the optional debug-dump filesystem I/O; everything numeric lives here.
//!
//! Default ordering: filter_low_quality -> filter_bob_markers ->
//! deskew_detections -> sort_reading_order.

use regex::Regex;
use std::cmp::Ordering;
use std::sync::OnceLock;

pub const MIN_CONFIDENCE: f64 = 0.7;
pub const MIN_TEXT_LENGTH: usize = 2;

// Detection-level deskew via RANSAC over same-row item<->price slopes.
// See docs/detection_deskew_plan.md for derivation.
pub const DESKEW_MIN_CONFIDENCE: f64 = 0.95;
pub const DESKEW_MIN_ITEM_WIDTH: f64 = 0.08; // x image_width
pub const DESKEW_MIN_PRICE_WIDTH: f64 = 0.03;
pub const DESKEW_MIN_X_DISTANCE: f64 = 0.50;
pub const DESKEW_ITEM_X_MAX_FRAC: f64 = 0.40;
pub const DESKEW_PRICE_X_MIN_FRAC: f64 = 0.60;
pub const DESKEW_Y_WINDOW_PX: f64 = 200.0;
pub const DESKEW_ANGLE_CAP_DEG: f64 = 5.0;
pub const DESKEW_MIN_ANGLE_DEG: f64 = 0.3;
pub const DESKEW_INLIER_TOL_DEG: f64 = 0.2;
pub const DESKEW_MIN_INLIERS: usize = 5;
pub const DESKEW_MIN_CONSENSUS: f64 = 0.60;
pub const DESKEW_RANSAC_ITERS: usize = 50;
pub const DESKEW_RANSAC_SEED: u64 = 0;

/// Numeric view of a detection. Field names mirror the Python detection dict.
#[derive(Clone, Debug, Default)]
pub struct Detection {
    pub confidence: f64,
    pub text: String,
    pub center_y: f64,
    pub y_min: f64,
    pub y_max: f64,
    pub min_x: f64,
    pub bbox: Vec<(f64, f64)>,
}

fn price_text_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\s*[-$]?\d+\.\d{2}[A-Z]?\s*$").unwrap())
}

/// Vertical overlap ratio test between two detections.
pub fn boxes_overlap_y(a: &Detection, b: &Detection, min_overlap_ratio: f64) -> bool {
    let overlap_start = a.y_min.max(b.y_min);
    let overlap_end = a.y_max.min(b.y_max);
    if overlap_start >= overlap_end {
        return false;
    }
    let overlap = overlap_end - overlap_start;
    let smaller_height = (a.y_max - a.y_min).min(b.y_max - b.y_min);
    if smaller_height <= 0.0 {
        return false;
    }
    overlap / smaller_height >= min_overlap_ratio
}

/// True for Costco Bottom-Of-Basket marker rows.
fn is_bob_marker_text(text: &str) -> bool {
    let upper = text.to_uppercase();
    let has_bottom_banner = upper.contains("BOTTOM OF BAS");
    let has_bob_count_marker = upper.contains("BOB COUNT") && has_xstar_run(&upper, 4);
    has_bottom_banner || has_bob_count_marker
}

/// Matches the `[X*]{4,}` clause: a run of `min_len`+ consecutive `X`/`*`.
fn has_xstar_run(text: &str, min_len: usize) -> bool {
    let mut run = 0usize;
    for ch in text.chars() {
        if ch == 'X' || ch == '*' {
            run += 1;
            if run >= min_len {
                return true;
            }
        } else {
            run = 0;
        }
    }
    false
}

/// Drop detections below the confidence floor or with too-short text.
/// Returns the kept indices in original order.
pub fn filter_low_quality(detections: &[Detection]) -> Vec<usize> {
    detections
        .iter()
        .enumerate()
        .filter(|(_, det)| {
            det.confidence >= MIN_CONFIDENCE && det.text.trim().chars().count() >= MIN_TEXT_LENGTH
        })
        .map(|(index, _)| index)
        .collect()
}

/// Drop Costco BOB markers that overlap real item rows. Returns kept indices.
pub fn filter_bob_markers(detections: &[Detection]) -> Vec<usize> {
    if detections.is_empty() {
        return Vec::new();
    }
    let mut kept: Vec<usize> = Vec::new();
    for (index, det) in detections.iter().enumerate() {
        if !is_bob_marker_text(&det.text) {
            kept.push(index);
            continue;
        }
        let overlaps_non_marker = detections.iter().enumerate().any(|(other_index, other)| {
            other_index != index
                && !is_bob_marker_text(&other.text)
                && boxes_overlap_y(det, other, 0.25)
        });
        if !overlaps_non_marker {
            kept.push(index);
        }
    }
    kept
}

fn bbox_x_extent(bbox: &[(f64, f64)]) -> (f64, f64, f64) {
    let mut x_min = f64::INFINITY;
    let mut x_max = f64::NEG_INFINITY;
    let mut sum = 0.0;
    for &(x, _) in bbox {
        x_min = x_min.min(x);
        x_max = x_max.max(x);
        sum += x;
    }
    (x_min, x_max, sum / bbox.len() as f64)
}

/// Cross-product item/price candidate angles, filtered by column/width/proximity.
///
/// Mispairings are expected to fall out as RANSAC outliers rather than being
/// filtered upfront. Only the implied tilt angle of each pair is retained.
fn build_pair_candidate_angles(detections: &[Detection], image_width: f64) -> Vec<f64> {
    let item_x_max_cap = image_width * DESKEW_ITEM_X_MAX_FRAC;
    let price_x_min_floor = image_width * DESKEW_PRICE_X_MIN_FRAC;
    let min_item_width = image_width * DESKEW_MIN_ITEM_WIDTH;
    let min_price_width = image_width * DESKEW_MIN_PRICE_WIDTH;
    let min_x_distance = image_width * DESKEW_MIN_X_DISTANCE;

    let mut items: Vec<(f64, f64)> = Vec::new(); // (x_center, center_y)
    let mut prices: Vec<(f64, f64)> = Vec::new(); // (x_center, center_y)

    for det in detections {
        if det.confidence < DESKEW_MIN_CONFIDENCE {
            continue;
        }
        if det.bbox.len() < 4 {
            continue;
        }
        let (x_min, x_max, x_center) = bbox_x_extent(&det.bbox);
        let width = x_max - x_min;
        if width <= 0.0 {
            continue;
        }
        let cy = det.center_y;
        let text = det.text.trim();

        if x_max < item_x_max_cap && width >= min_item_width {
            items.push((x_center, cy));
        }
        if x_min > price_x_min_floor && width >= min_price_width && price_text_re().is_match(text) {
            prices.push((x_center, cy));
        }
    }

    let mut candidates: Vec<f64> = Vec::new();
    for &(icx, icy) in &items {
        for &(pcx, pcy) in &prices {
            let dx = pcx - icx;
            if dx < min_x_distance {
                continue;
            }
            if (pcy - icy).abs() > DESKEW_Y_WINDOW_PX {
                continue;
            }
            candidates.push((pcy - icy).atan2(dx).to_degrees());
        }
    }
    candidates
}

/// Deterministic splitmix64 PRNG used for reproducible RANSAC sampling.
struct SplitMix64 {
    state: u64,
}

impl SplitMix64 {
    fn new(seed: u64) -> Self {
        Self { state: seed }
    }

    fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    fn below(&mut self, bound: usize) -> usize {
        (self.next_u64() % bound as u64) as usize
    }

    /// Three distinct indices in `[0, n)`; caller guarantees `n >= 3`.
    fn sample3(&mut self, n: usize) -> [usize; 3] {
        let a = self.below(n);
        let mut b = self.below(n);
        while b == a {
            b = self.below(n);
        }
        let mut c = self.below(n);
        while c == a || c == b {
            c = self.below(n);
        }
        [a, b, c]
    }
}

fn median3(a: f64, b: f64, c: f64) -> f64 {
    a.max(b).min(a.max(c)).max(b.min(c))
}

/// RANSAC over candidate angles: returns (best_angle_deg, inlier_count).
///
/// Deterministic via `DESKEW_RANSAC_SEED` so a given input always yields the
/// same output. Unlike the prior Python implementation it uses a splitmix64
/// PRNG instead of CPython's Mersenne Twister; consensus over inliers converges
/// to the same tilt within the pipeline's tolerance.
fn ransac_consensus(candidates: &[f64]) -> (f64, usize) {
    if candidates.len() < 3 {
        return (0.0, 0);
    }
    let mut rng = SplitMix64::new(DESKEW_RANSAC_SEED);
    let mut best_angle = 0.0;
    let mut best_inliers = 0usize;
    for _ in 0..DESKEW_RANSAC_ITERS {
        let [i, j, k] = rng.sample3(candidates.len());
        let trial = median3(candidates[i], candidates[j], candidates[k]);
        if trial.abs() > DESKEW_ANGLE_CAP_DEG {
            continue;
        }
        let mut sum = 0.0;
        let mut count = 0usize;
        for &angle in candidates {
            if (angle - trial).abs() <= DESKEW_INLIER_TOL_DEG {
                sum += angle;
                count += 1;
            }
        }
        if count > best_inliers {
            best_inliers = count;
            best_angle = sum / count as f64;
        }
    }
    (best_angle, best_inliers)
}

/// New `(center_y, y_min, y_max)` per detection after vertical shear correction.
fn apply_shear(detections: &[Detection], angle_deg: f64, image_width: f64) -> Vec<(f64, f64, f64)> {
    let tan_angle = angle_deg.to_radians().tan();
    let x_ref = image_width / 2.0;
    detections
        .iter()
        .map(|det| {
            let count = det.bbox.len().max(1) as f64;
            let x_center = det.bbox.iter().map(|&(x, _)| x).sum::<f64>() / count;
            let delta = (x_center - x_ref) * tan_angle;
            (det.center_y - delta, det.y_min - delta, det.y_max - delta)
        })
        .collect()
}

/// Result of the deskew pass. `new_y` is `Some` only when the shear is applied.
pub struct DeskewOutcome {
    pub angle_deg: f64,
    pub applied: bool,
    pub gate_reason: Option<&'static str>,
    pub candidate_count: usize,
    pub inlier_count: usize,
    pub consensus_ratio: f64,
    pub new_y: Option<Vec<(f64, f64, f64)>>,
}

/// Vertical shear correction driven by same-row item<->price slopes.
///
/// Bias is "miss safely": a wrong correction can push borderline rows out of
/// the matcher's y-band, so the pass only fires when consensus is strong, the
/// angle is in band, and large enough to matter.
pub fn deskew(detections: &[Detection], image_width: f64) -> DeskewOutcome {
    let candidates = build_pair_candidate_angles(detections, image_width);
    let candidate_count = candidates.len();
    let (angle, inliers) = ransac_consensus(&candidates);
    let consensus_ratio = if candidate_count > 0 {
        inliers as f64 / candidate_count as f64
    } else {
        0.0
    };

    let gate_reason = if candidate_count == 0 {
        Some("no_candidates")
    } else if inliers < DESKEW_MIN_INLIERS {
        Some("too_few_inliers")
    } else if angle.abs() > DESKEW_ANGLE_CAP_DEG {
        Some("angle_too_large")
    } else if consensus_ratio < DESKEW_MIN_CONSENSUS {
        Some("weak_consensus")
    } else if angle.abs() < DESKEW_MIN_ANGLE_DEG {
        Some("angle_too_small")
    } else {
        None
    };

    if gate_reason.is_some() {
        return DeskewOutcome {
            angle_deg: angle,
            applied: false,
            gate_reason,
            candidate_count,
            inlier_count: inliers,
            consensus_ratio,
            new_y: None,
        };
    }

    let new_y = apply_shear(detections, angle, image_width);
    DeskewOutcome {
        angle_deg: angle,
        applied: true,
        gate_reason: None,
        candidate_count,
        inlier_count: inliers,
        consensus_ratio,
        new_y: Some(new_y),
    }
}

/// Stable sort by (center_y, min_x) for top-to-bottom, left-to-right reading
/// order. Returns the source indices in sorted order.
pub fn sort_reading_order(detections: &[Detection]) -> Vec<usize> {
    let mut order: Vec<usize> = (0..detections.len()).collect();
    order.sort_by(|&a, &b| {
        detections[a]
            .center_y
            .partial_cmp(&detections[b].center_y)
            .unwrap_or(Ordering::Equal)
            .then(
                detections[a]
                    .min_x
                    .partial_cmp(&detections[b].min_x)
                    .unwrap_or(Ordering::Equal),
            )
    });
    order
}

#[cfg(test)]
mod tests {
    use super::*;

    fn det(text: &str, cx: f64, cy: f64, width: f64) -> Detection {
        let x_min = cx - width / 2.0;
        let x_max = cx + width / 2.0;
        let half_h = 15.0;
        Detection {
            confidence: 0.99,
            text: text.to_string(),
            center_y: cy,
            y_min: cy - half_h,
            y_max: cy + half_h,
            min_x: x_min,
            bbox: vec![
                (x_min, cy - half_h),
                (x_max, cy - half_h),
                (x_max, cy + half_h),
                (x_min, cy + half_h),
            ],
        }
    }

    fn straight_rows(n: usize) -> Vec<Detection> {
        let mut rows = Vec::new();
        for i in 0..n {
            let cy = 400.0 + i as f64 * 250.0;
            rows.push(det(&format!("ITEM {i}"), 200.0, cy, 200.0));
            rows.push(det(&format!("{:.2}", (i + 1) as f64 * 1.99), 850.0, cy, 80.0));
        }
        rows
    }

    fn tilt(d: &Detection, angle_deg: f64, image_width: f64) -> Detection {
        let tan_a = angle_deg.to_radians().tan();
        let x_ref = image_width / 2.0;
        let x_center = d.bbox.iter().map(|&(x, _)| x).sum::<f64>() / d.bbox.len() as f64;
        let delta = (x_center - x_ref) * tan_a;
        let mut new = d.clone();
        new.bbox = d.bbox.iter().map(|&(x, y)| (x, y + delta)).collect();
        new.center_y = d.center_y + delta;
        new.y_min = d.y_min + delta;
        new.y_max = d.y_max + delta;
        new
    }

    #[test]
    fn filter_low_quality_drops_low_confidence_and_short_text() {
        let mut dets = straight_rows(1);
        dets.push(det("LOW", 200.0, 9000.0, 200.0));
        dets.last_mut().unwrap().confidence = 0.5;
        dets.push(det("x", 200.0, 9500.0, 200.0)); // single char -> dropped
        let kept = filter_low_quality(&dets);
        assert_eq!(kept, vec![0, 1]);
    }

    #[test]
    fn deskew_no_candidates_when_empty() {
        let outcome = deskew(&[], 1000.0);
        assert!(!outcome.applied);
        assert_eq!(outcome.gate_reason, Some("no_candidates"));
        assert_eq!(outcome.candidate_count, 0);
    }

    #[test]
    fn deskew_no_op_on_straight_receipt() {
        let outcome = deskew(&straight_rows(8), 1000.0);
        assert!(!outcome.applied);
        assert_eq!(outcome.gate_reason, Some("angle_too_small"));
        assert!(outcome.angle_deg.abs() < DESKEW_MIN_ANGLE_DEG);
    }

    #[test]
    fn deskew_recovers_known_tilt() {
        let true_angle = 1.5;
        let straight = straight_rows(8);
        let tilted: Vec<Detection> =
            straight.iter().map(|d| tilt(d, true_angle, 1000.0)).collect();
        let outcome = deskew(&tilted, 1000.0);
        assert!(outcome.applied);
        assert!(outcome.inlier_count >= 5);
        assert!(outcome.consensus_ratio >= 0.60);
        assert!((outcome.angle_deg - true_angle).abs() < 0.05);
        let new_y = outcome.new_y.unwrap();
        for i in 0..8 {
            assert!((new_y[2 * i].0 - new_y[2 * i + 1].0).abs() < 1.0);
        }
    }

    #[test]
    fn deskew_rejects_large_angle() {
        let huge = DESKEW_ANGLE_CAP_DEG + 2.0;
        let tilted: Vec<Detection> = straight_rows(8)
            .iter()
            .map(|d| tilt(d, huge, 1000.0))
            .collect();
        let outcome = deskew(&tilted, 1000.0);
        assert!(!outcome.applied);
    }

    #[test]
    fn deskew_requires_price_text_shape() {
        let straight = straight_rows(8);
        let mut tilted: Vec<Detection> = straight.iter().map(|d| tilt(d, 1.5, 1000.0)).collect();
        for i in (1..tilted.len()).step_by(2) {
            tilted[i].text = "TAX".to_string();
        }
        let outcome = deskew(&tilted, 1000.0);
        assert_eq!(outcome.gate_reason, Some("no_candidates"));
    }

    #[test]
    fn bob_marker_detection() {
        assert!(is_bob_marker_text("***Bottom of Basket"));
        assert!(is_bob_marker_text("*xBOB Count XXXX"));
        assert!(!is_bob_marker_text("BOB Count 3"));
        assert!(!is_bob_marker_text("MILK"));
    }

    #[test]
    fn sort_reading_order_is_stable_top_to_bottom() {
        let dets = vec![
            det("B", 200.0, 100.0, 50.0),
            det("A", 100.0, 100.0, 50.0),
            det("C", 150.0, 50.0, 50.0),
        ];
        // center_y 50 first (index 2), then row at 100 ordered by min_x: A before B
        assert_eq!(sort_reading_order(&dets), vec![2, 1, 0]);
    }
}
