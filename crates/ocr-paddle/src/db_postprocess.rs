//! DB (Differentiable Binarization) post-processing, faithful to the desktop
//! `DBPostProcess` config: thresh 0.3, box_thresh 0.6, max_candidates 1000,
//! unclip_ratio 1.5.
//!
//! Pipeline (mirrors PaddleOCR `boxes_from_bitmap`):
//!   prob>thresh -> bitmap -> external contours -> min-area quad ->
//!   drop tiny -> mean-prob score filter -> unclip (expand) -> map to original px.
//!
//! `unclip` is approximated by growing the rotated rectangle outward by
//! `area*ratio/perimeter` on every side (PaddleOCR offsets the polygon with
//! round joins then re-fits a min-area rect, which for a rectangle is equivalent
//! to this growth). Exact pyclipper parity, if ever needed, is a later refinement.

use geo::MinimumRotatedRect;
use geo::{Coord, MultiPoint, Point, Polygon};
use image::GrayImage;
use imageproc::contours::{find_contours, BorderType};

#[derive(Clone, Copy, Debug)]
pub struct DbConfig {
    pub thresh: f32,
    pub box_thresh: f32,
    pub max_candidates: usize,
    pub unclip_ratio: f32,
    pub min_size: f32,
}

impl Default for DbConfig {
    fn default() -> Self {
        // PP-OCRv5_mobile_det_infer/inference.yml
        Self {
            thresh: 0.3,
            box_thresh: 0.6,
            max_candidates: 1000,
            unclip_ratio: 1.5,
            min_size: 3.0,
        }
    }
}

/// A detected quadrilateral in original-image pixel coordinates, ordered
/// top-left, top-right, bottom-right, bottom-left.
#[derive(Clone, Copy, Debug)]
pub struct Quad {
    pub points: [[f32; 2]; 4],
}

/// Extract text-region quads from a DB probability map.
///
/// `prob` is the `h*w` row-major probability map at the *resized* detection
/// dims; `ratio_w`/`ratio_h` are `resized/original` (from `DetInput`) used to map
/// boxes back to original pixels of size `orig_w`/`orig_h`.
pub fn boxes_from_bitmap(
    prob: &[f32],
    h: usize,
    w: usize,
    orig_w: f32,
    orig_h: f32,
    ratio_w: f32,
    ratio_h: f32,
    cfg: &DbConfig,
) -> Vec<Quad> {
    debug_assert_eq!(prob.len(), h * w);

    // Binary bitmap for contour finding (nonzero = foreground).
    let mut bitmap = GrayImage::new(w as u32, h as u32);
    for y in 0..h {
        for x in 0..w {
            if prob[y * w + x] > cfg.thresh {
                bitmap.put_pixel(x as u32, y as u32, image::Luma([255]));
            }
        }
    }

    let contours = find_contours::<i32>(&bitmap);
    let mut quads = Vec::new();
    for contour in contours.into_iter().filter(|c| c.border_type == BorderType::Outer) {
        if quads.len() >= cfg.max_candidates {
            break;
        }
        if contour.points.len() < 4 {
            continue;
        }

        let Some((corners, min_side)) = min_area_quad(&contour.points) else {
            continue;
        };
        if min_side < cfg.min_size {
            continue;
        }
        if box_score(prob, w, h, &corners) < cfg.box_thresh {
            continue;
        }

        let expanded = unclip(&corners, cfg.unclip_ratio);
        let Some((mut box_pts, min_side2)) = min_area_quad_from_pts(&expanded) else {
            continue;
        };
        if min_side2 < cfg.min_size + 2.0 {
            continue;
        }

        // Map resized-pixel coords back to the original image and clip.
        for p in box_pts.iter_mut() {
            p[0] = (p[0] / ratio_w).clamp(0.0, orig_w);
            p[1] = (p[1] / ratio_h).clamp(0.0, orig_h);
        }
        quads.push(Quad {
            points: order_clockwise(box_pts),
        });
    }
    quads
}

/// Minimum-area rotated rectangle around integer contour points -> 4 corners +
/// shorter side length.
fn min_area_quad(points: &[imageproc::point::Point<i32>]) -> Option<([[f32; 2]; 4], f32)> {
    let mp: MultiPoint<f64> = points
        .iter()
        .map(|p| Point::new(p.x as f64, p.y as f64))
        .collect();
    let rect: Polygon<f64> = mp.minimum_rotated_rect()?;
    quad_from_polygon(&rect)
}

fn min_area_quad_from_pts(pts: &[[f32; 2]; 4]) -> Option<([[f32; 2]; 4], f32)> {
    let mp: MultiPoint<f64> = pts
        .iter()
        .map(|p| Point::new(p[0] as f64, p[1] as f64))
        .collect();
    let rect: Polygon<f64> = mp.minimum_rotated_rect()?;
    quad_from_polygon(&rect)
}

fn quad_from_polygon(rect: &Polygon<f64>) -> Option<([[f32; 2]; 4], f32)> {
    let coords: Vec<Coord<f64>> = rect.exterior().coords().copied().collect();
    if coords.len() < 4 {
        return None;
    }
    let c = |i: usize| [coords[i].x as f32, coords[i].y as f32];
    let corners = [c(0), c(1), c(2), c(3)];
    let side = |a: [f32; 2], b: [f32; 2]| ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt();
    let min_side = side(corners[0], corners[1]).min(side(corners[1], corners[2]));
    Some((corners, min_side))
}

/// Mean probability inside the quad's axis-aligned bounding box, masked by the
/// quad. Mirrors PaddleOCR `box_score_fast`.
fn box_score(prob: &[f32], w: usize, h: usize, quad: &[[f32; 2]; 4]) -> f32 {
    let xs = quad.iter().map(|p| p[0]);
    let ys = quad.iter().map(|p| p[1]);
    let xmin = xs.clone().fold(f32::INFINITY, f32::min).floor().max(0.0) as usize;
    let xmax = (xs.fold(f32::NEG_INFINITY, f32::max).ceil() as usize).min(w.saturating_sub(1));
    let ymin = ys.clone().fold(f32::INFINITY, f32::min).floor().max(0.0) as usize;
    let ymax = (ys.fold(f32::NEG_INFINITY, f32::max).ceil() as usize).min(h.saturating_sub(1));
    if xmax < xmin || ymax < ymin {
        return 0.0;
    }
    let mut sum = 0.0;
    let mut count = 0u32;
    for y in ymin..=ymax {
        for x in xmin..=xmax {
            if point_in_quad(x as f32 + 0.5, y as f32 + 0.5, quad) {
                sum += prob[y * w + x];
                count += 1;
            }
        }
    }
    if count == 0 {
        0.0
    } else {
        sum / count as f32
    }
}

/// Grow the rotated rectangle outward by `area*ratio/perimeter` on each side.
fn unclip(quad: &[[f32; 2]; 4], ratio: f32) -> [[f32; 2]; 4] {
    let center = [
        quad.iter().map(|p| p[0]).sum::<f32>() / 4.0,
        quad.iter().map(|p| p[1]).sum::<f32>() / 4.0,
    ];
    let len = |a: [f32; 2], b: [f32; 2]| ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt();
    let w = len(quad[0], quad[1]);
    let hgt = len(quad[1], quad[2]);
    let area = w * hgt;
    let perim = 2.0 * (w + hgt);
    if perim <= f32::EPSILON {
        return *quad;
    }
    let distance = area * ratio / perim;

    // Unit axes of the rectangle.
    let unit = |a: [f32; 2], b: [f32; 2]| {
        let d = len(a, b).max(f32::EPSILON);
        [(b[0] - a[0]) / d, (b[1] - a[1]) / d]
    };
    let u = unit(quad[0], quad[1]);
    let v = unit(quad[1], quad[2]);
    let hw = w / 2.0 + distance;
    let hh = hgt / 2.0 + distance;
    [
        [
            center[0] - hw * u[0] - hh * v[0],
            center[1] - hw * u[1] - hh * v[1],
        ],
        [
            center[0] + hw * u[0] - hh * v[0],
            center[1] + hw * u[1] - hh * v[1],
        ],
        [
            center[0] + hw * u[0] + hh * v[0],
            center[1] + hw * u[1] + hh * v[1],
        ],
        [
            center[0] - hw * u[0] + hh * v[0],
            center[1] - hw * u[1] + hh * v[1],
        ],
    ]
}

fn point_in_quad(px: f32, py: f32, quad: &[[f32; 2]; 4]) -> bool {
    // Convex polygon: point is inside if it's on the same side of every edge.
    let mut sign = 0.0f32;
    for i in 0..4 {
        let a = quad[i];
        let b = quad[(i + 1) % 4];
        let cross = (b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0]);
        if cross.abs() > f32::EPSILON {
            if sign == 0.0 {
                sign = cross.signum();
            } else if cross.signum() != sign {
                return false;
            }
        }
    }
    true
}

/// Order 4 points as top-left, top-right, bottom-right, bottom-left.
fn order_clockwise(pts: [[f32; 2]; 4]) -> [[f32; 2]; 4] {
    let tl = *pts.iter().min_by(|a, b| (a[0] + a[1]).total_cmp(&(b[0] + b[1]))).unwrap();
    let br = *pts.iter().max_by(|a, b| (a[0] + a[1]).total_cmp(&(b[0] + b[1]))).unwrap();
    let tr = *pts.iter().min_by(|a, b| (a[1] - a[0]).total_cmp(&(b[1] - b[0]))).unwrap();
    let bl = *pts.iter().max_by(|a, b| (a[1] - a[0]).total_cmp(&(b[1] - b[0]))).unwrap();
    [tl, tr, br, bl]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_single_box_for_one_high_prob_rectangle() {
        // 100x60 prob map with a high-probability filled rectangle [20..80]x[20..40].
        let (w, h) = (100usize, 60usize);
        let mut prob = vec![0f32; w * h];
        for y in 20..40 {
            for x in 20..80 {
                prob[y * w + x] = 0.9;
            }
        }
        // ratios 1.0 -> original == resized dims.
        let quads = boxes_from_bitmap(
            &prob, h, w, w as f32, h as f32, 1.0, 1.0, &DbConfig::default(),
        );
        assert_eq!(quads.len(), 1, "expected exactly one detected box");
        let q = quads[0];
        // Box should roughly cover the rectangle (unclip expands it a bit).
        let xs: Vec<f32> = q.points.iter().map(|p| p[0]).collect();
        let ys: Vec<f32> = q.points.iter().map(|p| p[1]).collect();
        let xmin = xs.iter().cloned().fold(f32::INFINITY, f32::min);
        let xmax = xs.iter().cloned().fold(f32::NEG_INFINITY, f32::max);
        let ymin = ys.iter().cloned().fold(f32::INFINITY, f32::min);
        let ymax = ys.iter().cloned().fold(f32::NEG_INFINITY, f32::max);
        assert!(xmin < 25.0 && xmax > 75.0, "x span off: {xmin}..{xmax}");
        assert!(ymin < 25.0 && ymax > 35.0, "y span off: {ymin}..{ymax}");
    }

    #[test]
    fn drops_low_probability_regions() {
        let (w, h) = (100usize, 60usize);
        let mut prob = vec![0f32; w * h];
        // below box_thresh (0.6) -> filtered out
        for y in 20..40 {
            for x in 20..80 {
                prob[y * w + x] = 0.45;
            }
        }
        let quads = boxes_from_bitmap(
            &prob, h, w, w as f32, h as f32, 1.0, 1.0, &DbConfig::default(),
        );
        assert!(quads.is_empty(), "low-prob region should be dropped");
    }

    #[test]
    fn order_clockwise_sorts_corners() {
        let pts = [[10.0, 10.0], [0.0, 10.0], [0.0, 0.0], [10.0, 0.0]];
        let ordered = order_clockwise(pts);
        assert_eq!(ordered[0], [0.0, 0.0]); // TL
        assert_eq!(ordered[2], [10.0, 10.0]); // BR
    }
}
