#[derive(Clone, Debug)]
pub(crate) struct SpatialLineCandidate {
    line_y: f64,
    is_used: bool,
    is_valid_item_line: bool,
    has_trailing_price: bool,
    looks_like_quantity_expression: bool,
}

impl SpatialLineCandidate {
    pub(crate) fn new(
        line_y: f64,
        is_used: bool,
        is_valid_item_line: bool,
        has_trailing_price: bool,
        looks_like_quantity_expression: bool,
    ) -> Self {
        Self {
            line_y,
            is_used,
            is_valid_item_line,
            has_trailing_price,
            looks_like_quantity_expression,
        }
    }
}

const SPATIAL_FLOAT_EPSILON: f64 = 1e-6;

pub(crate) fn select_spatial_item_line(
    price_y: f64,
    y_tolerance: f64,
    max_item_distance: f64,
    prefer_below: bool,
    price_line_has_onsale: bool,
    candidates: Vec<SpatialLineCandidate>,
) -> Option<(usize, f64)> {
    let mut closest: Option<(usize, f64)> = None;

    let update_closest = |closest: &mut Option<(usize, f64)>, index: usize, distance: f64| {
        if let Some((_, current_distance)) = closest {
            if distance < *current_distance {
                *closest = Some((index, distance));
            }
        } else {
            *closest = Some((index, distance));
        }
    };

    for (index, candidate) in candidates.iter().enumerate() {
        let distance = (candidate.line_y - price_y).abs();
        if candidate.is_used
            || !candidate.is_valid_item_line
            || distance > y_tolerance + SPATIAL_FLOAT_EPSILON
            || !candidate.has_trailing_price
            || candidate.looks_like_quantity_expression
        {
            continue;
        }
        update_closest(&mut closest, index, distance);
    }
    if closest.is_some() {
        return closest;
    }

    if prefer_below {
        for (index, candidate) in candidates.iter().enumerate() {
            if candidate.is_used || !candidate.is_valid_item_line {
                continue;
            }
            if candidate.line_y < price_y
                || candidate.line_y - price_y > max_item_distance + SPATIAL_FLOAT_EPSILON
            {
                continue;
            }
            let distance = (candidate.line_y - price_y).abs();
            update_closest(&mut closest, index, distance);
        }
        if closest.is_some() {
            return closest;
        }
    }

    for (index, candidate) in candidates.iter().enumerate() {
        if candidate.is_used || !candidate.is_valid_item_line {
            continue;
        }
        if candidate.line_y > price_y
            || price_y - candidate.line_y > max_item_distance + SPATIAL_FLOAT_EPSILON
        {
            continue;
        }
        if price_line_has_onsale && candidate.line_y < price_y && candidate.has_trailing_price {
            continue;
        }
        let distance = (candidate.line_y - price_y).abs();
        update_closest(&mut closest, index, distance);
    }
    if closest.is_some() {
        return closest;
    }

    let same_row_below_tolerance = y_tolerance * 2.0;
    for (index, candidate) in candidates.iter().enumerate() {
        if candidate.is_used || !candidate.is_valid_item_line {
            continue;
        }
        if candidate.line_y <= price_y
            || candidate.line_y > price_y + same_row_below_tolerance + SPATIAL_FLOAT_EPSILON
        {
            continue;
        }
        let distance = (candidate.line_y - price_y).abs();
        update_closest(&mut closest, index, distance);
    }

    closest
}

#[cfg(test)]
mod tests {
    use super::{select_spatial_item_line, SpatialLineCandidate};

    #[test]
    fn prefers_same_row_priced_item_over_other_candidates() {
        let candidates = vec![
            SpatialLineCandidate::new(0.10, false, true, false, false),
            SpatialLineCandidate::new(0.20, false, true, true, false),
            SpatialLineCandidate::new(0.24, false, true, true, false),
        ];

        let selected =
            select_spatial_item_line(0.205, 0.02, 0.08, false, false, candidates).unwrap();

        assert_eq!(selected.0, 1);
    }

    #[test]
    fn prefers_below_when_header_row_requests_it() {
        let candidates = vec![
            SpatialLineCandidate::new(0.20, false, false, true, false),
            SpatialLineCandidate::new(0.24, false, true, false, false),
            SpatialLineCandidate::new(0.26, false, true, false, false),
        ];

        let selected = select_spatial_item_line(0.21, 0.01, 0.08, true, false, candidates).unwrap();

        assert_eq!(selected.0, 1);
    }

    #[test]
    fn skips_priced_lines_above_for_onsale_rows() {
        let candidates = vec![
            SpatialLineCandidate::new(0.19, false, true, true, false),
            SpatialLineCandidate::new(0.21, false, true, false, false),
        ];

        let selected = select_spatial_item_line(0.20, 0.01, 0.08, false, true, candidates).unwrap();

        assert_eq!(selected.0, 1);
    }

    #[test]
    fn prefer_below_picks_nearest_valid_item() {
        // Simulates T&T layout: FOOD (header) has price $3.80, next line
        // SALTED EGG YOLK has its own price $6.27, BLUSH BERRY has no price.
        // prefer_below should pick the nearest valid item (SALTED EGG YOLK).
        // When $6.27 processes later, SALTED EGG YOLK is already used so
        // it falls through to BLUSH BERRY.
        let candidates = vec![
            SpatialLineCandidate::new(0.192, false, false, true, false), // FOOD header (invalid)
            SpatialLineCandidate::new(0.210, false, true, true, false),  // SALTED EGG YOLK
            SpatialLineCandidate::new(0.224, false, true, false, false), // BLUSH BERRY
        ];

        let selected = select_spatial_item_line(0.203, 0.02, 0.08, true, false, candidates).unwrap();

        assert_eq!(selected.0, 1); // Nearest valid item below: SALTED EGG YOLK
    }

    #[test]
    fn source_line_anchor_allows_unpriced_neighbor_to_beat_next_priced_row() {
        let candidates = vec![
            SpatialLineCandidate::new(0.165, true, true, false, false),
            SpatialLineCandidate::new(0.185, false, false, false, true),
            SpatialLineCandidate::new(0.193, false, true, false, false),
            SpatialLineCandidate::new(0.211, false, true, true, false),
        ];

        let selected =
            select_spatial_item_line(0.185, 0.02, 0.08, false, false, candidates).unwrap();

        assert_eq!(selected.0, 2);
    }
}
