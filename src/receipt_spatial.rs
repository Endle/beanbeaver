use regex::Regex;
use std::sync::OnceLock;

const SCALE: i64 = 10_000;
const MIN_CONFIDENCE: f64 = 0.5;
const PRICE_X_THRESHOLD: f64 = 0.65;
const ITEM_X_THRESHOLD: f64 = 0.6;
const Y_TOLERANCE: f64 = 0.02;
const MAX_ITEM_DISTANCE: f64 = 0.08;
const SPATIAL_FLOAT_EPSILON: f64 = 1e-6;

#[derive(Clone, Debug)]
pub(crate) struct BboxInput {
    pub(crate) left: f64,
    pub(crate) top: f64,
    pub(crate) right: f64,
    pub(crate) bottom: f64,
}

#[derive(Clone, Debug)]
pub(crate) struct WordInput {
    pub(crate) text: String,
    pub(crate) bbox: BboxInput,
    pub(crate) confidence: f64,
}

#[derive(Clone, Debug)]
pub(crate) struct LineInput {
    pub(crate) text: String,
    pub(crate) words: Vec<WordInput>,
}

#[derive(Clone, Debug)]
pub(crate) struct PageInput {
    pub(crate) lines: Vec<LineInput>,
}

#[derive(Clone, Debug)]
pub(crate) struct SpatialExtractedItem {
    pub(crate) description: String,
    pub(crate) price_scaled: i64,
}

#[derive(Clone, Debug)]
pub(crate) struct SpatialParserWarning {
    pub(crate) message: String,
    pub(crate) after_item_index: Option<usize>,
}

#[derive(Clone, Debug)]
pub(crate) struct SpatialExtractionOutcome {
    pub(crate) items: Vec<SpatialExtractedItem>,
    pub(crate) warnings: Vec<SpatialParserWarning>,
}

#[derive(Clone, Debug)]
struct ParsedLine {
    line_y: f64,
    full_text: String,
    left_text: String,
}

#[derive(Clone, Debug)]
struct PriceCandidate {
    price_y: f64,
    price_scaled: i64,
    source_line_index: usize,
}

fn re_digits_dots_only() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[\d.]+$").unwrap())
}

fn re_long_digits_only() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\d{8,}\s*$").unwrap())
}

fn re_standalone_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\$?\d+\.\d{2}\s*$").unwrap())
}

fn re_trailing_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(\d+\.\d{2})(-?)\s*[HhTtJj]?\s*$").unwrap())
}

fn re_weight_info() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\d+\.\d+\s*kg").unwrap())
}

fn re_w_dollar() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^W\s*\$").unwrap())
}

fn re_malformed_ocr_prefix() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\(H{1,2}E[DI]?\b").unwrap())
}

fn re_multibuy_parenthetical() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\(\d+\s*/\s*for\s+\$[\d.]+\)").unwrap())
}

fn re_short_parenthetical_code() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\([^)]{1,5}\)").unwrap())
}

fn re_footer_address_patterns() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"\b(AVE|AVENUE|ST|STREET|RD|ROAD|BLVD|BOULEVARD|DR|DRIVE|HWY|HIGHWAY)\b|\b(MARKHAM|TORONTO|MISSISSAUGA|RICHMOND\s+HILL|ON|ONTARIO)\b|\b(L\d[A-Z]\d)\b|\(\d{3}\)\s*\d{3}-\d{4}",
        )
        .unwrap()
    })
}

fn re_receipt_metadata_patterns() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)WS#|RECEIPT#|CASHIER|ITEM\s+COUNT|NUMBER\s+OF\s+ITEMS|HAPPY\s+SHOPPING|CREDIT\s+CARD|DEBIT|APPROVED|AUTH|REFERENCE|TERMINAL|CUSTOMER\s+COPY",
        )
        .unwrap()
    })
}

fn re_count_at_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^\d+\s*@\s*\$?-?\d+\.\d{2}").unwrap())
}

fn re_weight_at_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\d+\.?\d*\s*(?:lb|lk|kg|k[g9]|1b|1k)\s*@").unwrap())
}

fn re_multi_for_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\(?\d+\s*/\s*for\s+\$?\d+\.\d{2}\)?").unwrap())
}

fn re_compact_offer_fragment() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\d+\s*@\s*\d+\s*/\s*\$?\d+\.\d{2}\b").unwrap())
}

fn re_parenthetical_offer_prefix() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\([^)]+\)\s+\d+\s*/\s*for\b").unwrap())
}

fn re_section_header_with_aisle() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[^A-Z0-9]*\d{1,2}\s*[-:]\s*[A-Z]{3,}$").unwrap())
}

fn re_summary_patterns() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"^(?:SUB\s*TOTAL|SUBTOTAL|TOTAL|HST|GST|PST|TAX|MASTER(?:CARD)?|VISA|DEBIT|CREDIT|POINTS|CASH|CHANGE|BALANCE|APPROVED|CARD|TERMINAL|MEMBER)\b",
        )
        .unwrap()
    })
}

fn re_tax_tokens() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\b(HST|GST|PST|TAX)\b").unwrap())
}

fn re_section_aisle_prefix() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[^A-Z0-9]*\d{1,2}\s*[-:]").unwrap())
}

fn re_leading_section_item_prefix() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)^[^A-Z0-9]*\d{1,2}\s*[-:]\s*(MEAT|SEAFOOD|PRODUCE|DELI|GROCERY|BAKERY|FROZEN)\b\s*",
        )
        .unwrap()
    })
}

fn re_ascii_words() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[A-Z]+").unwrap())
}

fn re_price_word() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\$?(\d+\.\d{2})$").unwrap())
}

fn re_embedded_trailing_price_word() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)[A-Z]{1,6}\$?(\d+\.\d{2})$").unwrap())
}

fn re_leading_qty_prefix() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\(\d+\)\s*").unwrap())
}

fn re_leading_long_sku() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\d{6,}[A-Za-z]?\s*").unwrap())
}

fn re_sale_marker() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\(SALE\)\s*").unwrap())
}

fn re_hed_marker() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\(HED[^)]*\)\s*").unwrap())
}

fn re_hhed_marker() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\(HHED[^)]*\)\s*").unwrap())
}

fn re_qty_price_marker() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"@?\d+/[A-Za-z]?\$?\d+\.\d{2}").unwrap())
}

fn re_qty_price_marker_2() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\d+/\$?\d+\.\d{2}").unwrap())
}

fn re_unit_price_marker() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\$\d+\.\d+/\w+").unwrap())
}

fn re_inline_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\$\d+\.\d{2}").unwrap())
}

fn re_garbled_price_artifact() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\d+s\d+\.\d+ea").unwrap())
}

fn re_cahrd() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\bCAHRD\b").unwrap())
}

fn re_hed_word() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\bHED\b").unwrap())
}

fn re_leading_non_alnum() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[^A-Za-z0-9]+").unwrap())
}

fn re_trailing_non_alnum() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[^A-Za-z0-9)]+$").unwrap())
}

fn re_multi_spaces() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\s+").unwrap())
}

fn normalize_decimal_spacing(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'.' && i > 0 && bytes[i - 1].is_ascii_digit() {
            let mut j = i + 1;
            while j < bytes.len() && bytes[j].is_ascii_whitespace() {
                j += 1;
            }
            if j > i + 1
                && j + 1 < bytes.len()
                && bytes[j].is_ascii_digit()
                && bytes[j + 1].is_ascii_digit()
                && (j + 2 == bytes.len() || !bytes[j + 2].is_ascii_digit())
            {
                out.push('.');
                out.push(bytes[j] as char);
                out.push(bytes[j + 1] as char);
                i = j + 2;
                continue;
            }
        }
        out.push(bytes[i] as char);
        i += 1;
    }
    out
}

fn parse_scaled_decimal(token: &str) -> Option<i64> {
    let trimmed = token.trim();
    let (whole, frac) = trimmed.split_once('.')?;
    if whole.is_empty() || frac.len() != 2 {
        return None;
    }
    if !whole.chars().all(|ch| ch.is_ascii_digit()) || !frac.chars().all(|ch| ch.is_ascii_digit()) {
        return None;
    }
    let whole_value = whole.parse::<i64>().ok()?;
    let frac_value = frac.parse::<i64>().ok()?;
    Some(whole_value * SCALE + frac_value * 100)
}

fn format_scaled_currency(value: i64) -> String {
    let abs_value = value.abs();
    let cents = abs_value / 100;
    let dollars = cents / 100;
    let rem = cents % 100;
    if value < 0 {
        format!("-{dollars}.{rem:02}")
    } else {
        format!("{dollars}.{rem:02}")
    }
}

fn alpha_ratio(value: &str) -> f64 {
    if value.is_empty() {
        return 0.0;
    }
    let alpha_count = value.chars().filter(|ch| ch.is_ascii_alphabetic()).count();
    alpha_count as f64 / value.len() as f64
}

fn is_section_name(text: &str) -> bool {
    matches!(
        text,
        "MEAT" | "SEAFOOD" | "PRODUCE" | "DELI" | "GROCERY" | "BAKERY" | "FROZEN"
    )
}

fn strip_leading_receipt_codes(text: &str) -> String {
    let trimmed = text.trim();
    let trimmed = re_leading_qty_prefix().replace(trimmed, "");
    let trimmed = re_leading_long_sku().replace(trimmed.as_ref(), "");
    let trimmed = re_leading_section_item_prefix().replace(trimmed.as_ref(), "");
    trimmed.trim().to_string()
}

fn is_section_header_text(text: &str) -> bool {
    if text.trim().is_empty() {
        return false;
    }
    let normalized = re_multi_spaces()
        .replace(&text.trim().to_ascii_uppercase(), " ")
        .to_string();
    if is_section_name(normalized.as_str()) {
        return true;
    }
    if re_section_header_with_aisle().is_match(&normalized) {
        return true;
    }
    if re_section_aisle_prefix().is_match(&normalized) {
        let remainder = re_section_aisle_prefix()
            .replace(&normalized, "")
            .trim()
            .to_string();
        let words = re_ascii_words()
            .find_iter(&remainder)
            .map(|m| m.as_str())
            .collect::<Vec<_>>();
        if words.len() == 1 && is_section_name(words[0]) {
            return true;
        }
    }
    false
}

fn is_summary_line(text: &str) -> bool {
    if text.trim().is_empty() {
        return false;
    }
    let upper = text.trim().to_ascii_uppercase();
    if re_summary_patterns().is_match(&upper) {
        return true;
    }
    if upper.contains("SUBTOTAL") || upper.contains("SUB TOTAL") || upper.contains("TOTAL") {
        return true;
    }
    if re_tax_tokens().is_match(&upper) {
        return true;
    }
    upper.starts_with("H=") && re_tax_tokens().is_match(&upper)
}

fn trailing_price_scaled(text: &str) -> Option<i64> {
    let normalized = normalize_decimal_spacing(text.trim());
    let captures = re_trailing_price().captures(&normalized)?;
    parse_scaled_decimal(captures.get(1)?.as_str())
}

fn line_has_trailing_price(text: &str) -> bool {
    trailing_price_scaled(text).is_some()
}

fn looks_like_onsale_marker(text: &str) -> bool {
    if text.trim().is_empty() {
        return false;
    }
    let normalized = normalize_decimal_spacing(&text.trim().to_ascii_uppercase());
    let without_price = re_trailing_price().replace(&normalized, "").to_string();
    let compact: String = without_price
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .collect();
    if compact.ends_with("ONSALE") || compact.ends_with("ONSAL") {
        let prefix_len = compact.len().saturating_sub(6);
        return prefix_len <= 3;
    }
    false
}

fn is_priced_generic_item_label(left_text: &str, full_text: &str) -> bool {
    if left_text.trim().is_empty() {
        return false;
    }
    line_has_trailing_price(full_text) && is_section_name(left_text.trim().to_ascii_uppercase().as_str())
}

fn parse_quantity_modifier(text: &str) -> bool {
    re_count_at_price().is_match(text)
        || re_weight_at_price().is_match(text)
        || re_multi_for_price().is_match(text)
}

fn looks_like_quantity_expression(text: &str) -> bool {
    let normalized = normalize_decimal_spacing(text.trim());
    if normalized.is_empty() {
        return false;
    }
    if parse_quantity_modifier(&normalized) {
        return true;
    }
    let upper = normalized.to_ascii_uppercase();
    if upper.starts_with('(') && upper.contains('@') && upper.contains("/$") {
        let alpha_count = upper.chars().filter(|ch| ch.is_ascii_alphabetic()).count();
        if alpha_count <= 2 {
            return true;
        }
    }
    if upper.contains('@') && upper.contains("/$") {
        let compact: String = upper
            .chars()
            .filter(|ch| !ch.is_ascii_whitespace())
            .collect();
        let alpha_count = compact
            .chars()
            .filter(|ch| ch.is_ascii_alphabetic())
            .count();
        let digit_count = compact.chars().filter(|ch| ch.is_ascii_digit()).count();
        if digit_count >= 3 && alpha_count <= 4 {
            return true;
        }
    }
    re_multi_for_price().is_match(&normalized)
        || re_compact_offer_fragment().is_match(&normalized)
        || re_parenthetical_offer_prefix().is_match(&normalized)
}

fn footer_address_like(text: &str) -> bool {
    re_footer_address_patterns().is_match(&text.to_ascii_uppercase())
}

fn receipt_metadata_like(text: &str) -> bool {
    re_receipt_metadata_patterns().is_match(text.trim())
}

fn clean_description(desc: &str) -> String {
    let mut cleaned = desc.to_string();
    cleaned = re_leading_qty_prefix().replace(&cleaned, "").to_string();
    cleaned = re_sale_marker().replace_all(&cleaned, "").to_string();
    cleaned = re_hed_marker().replace_all(&cleaned, "").to_string();
    cleaned = re_hhed_marker().replace_all(&cleaned, "").to_string();
    cleaned = re_qty_price_marker().replace_all(&cleaned, "").to_string();
    cleaned = re_qty_price_marker_2()
        .replace_all(&cleaned, "")
        .to_string();
    cleaned = re_unit_price_marker().replace_all(&cleaned, "").to_string();
    cleaned = re_inline_price().replace_all(&cleaned, "").to_string();
    cleaned = re_garbled_price_artifact()
        .replace_all(&cleaned, "")
        .to_string();
    cleaned = re_leading_long_sku().replace(&cleaned, "").to_string();
    cleaned = re_leading_section_item_prefix()
        .replace(&cleaned, "")
        .to_string();
    cleaned = re_cahrd().replace_all(&cleaned, "").to_string();
    cleaned = re_hed_word().replace_all(&cleaned, "").to_string();
    cleaned = re_leading_non_alnum().replace(&cleaned, "").to_string();
    cleaned = re_trailing_non_alnum().replace(&cleaned, "").to_string();
    cleaned = re_multi_spaces().replace_all(&cleaned, " ").to_string();
    cleaned.trim().to_string()
}

fn is_deposit_stub(text: &str) -> bool {
    let cleaned = clean_description(text);
    let upper = cleaned.to_ascii_uppercase();
    upper == "DEPOSIT" || upper.starts_with("DEPOSIT ")
}

fn lacks_description_context(text: &str) -> bool {
    let stripped = strip_leading_receipt_codes(text);
    stripped.is_empty() || alpha_ratio(&stripped) < 0.5
}

fn is_price_word(text: &str) -> Option<i64> {
    let normalized = normalize_decimal_spacing(text.trim());
    let stripped = normalized
        .strip_prefix('W')
        .map(str::trim_start)
        .or_else(|| normalized.strip_prefix('w').map(str::trim_start))
        .unwrap_or(normalized.as_str());
    if let Some(captures) = re_price_word().captures(stripped) {
        return parse_scaled_decimal(captures.get(1)?.as_str());
    }
    if stripped.contains('@') || stripped.contains('/') {
        return None;
    }
    let captures = re_embedded_trailing_price_word().captures(stripped)?;
    parse_scaled_decimal(captures.get(1)?.as_str())
}

fn is_short_alpha_item(text: &str) -> bool {
    let letters_only: String = text.chars().filter(|ch| ch.is_ascii_alphabetic()).collect();
    letters_only.len() >= 3 && letters_only.chars().all(|ch| ch.is_ascii_alphabetic())
}

fn is_valid_onsale_target(line: &ParsedLine) -> bool {
    if line.left_text.is_empty() {
        return false;
    }
    if receipt_metadata_like(&line.left_text) || receipt_metadata_like(&line.full_text) {
        return false;
    }
    if is_summary_line(&line.left_text) || is_summary_line(&line.full_text) {
        return false;
    }
    if is_section_header_text(&line.left_text) || is_section_header_text(&line.full_text) {
        return false;
    }
    if looks_like_quantity_expression(&line.left_text) {
        return false;
    }
    if line_has_trailing_price(&line.full_text) {
        return false;
    }
    let stripped = strip_leading_receipt_codes(&line.left_text);
    !stripped.is_empty() && alpha_ratio(&stripped) >= 0.5
}

fn is_valid_item_line(line: &ParsedLine, total_line_y: Option<f64>) -> bool {
    let left_text_for_ratio = strip_leading_receipt_codes(&line.left_text);
    if left_text_for_ratio.is_empty() || line.left_text.is_empty() {
        return false;
    }
    if receipt_metadata_like(&line.left_text) || receipt_metadata_like(&line.full_text) {
        return false;
    }
    let short_alpha = is_short_alpha_item(&left_text_for_ratio);
    if line.left_text.len() < 5
        && !is_priced_generic_item_label(&line.left_text, &line.full_text)
        && !short_alpha
    {
        return false;
    }
    if let Some(total_y) = total_line_y {
        if line.line_y > total_y + Y_TOLERANCE {
            return false;
        }
    }
    if is_summary_line(&line.left_text) || is_summary_line(&line.full_text) {
        return false;
    }
    let left_is_header = is_section_header_text(&line.left_text)
        && !is_priced_generic_item_label(&line.left_text, &line.full_text);
    if left_is_header || is_section_header_text(&line.full_text) {
        return false;
    }
    if re_long_digits_only().is_match(&line.full_text) {
        return false;
    }
    if alpha_ratio(&left_text_for_ratio) < 0.5 {
        return false;
    }
    if re_malformed_ocr_prefix().is_match(&line.left_text) {
        return false;
    }
    if line.left_text.len() < 8
        && !line.left_text.contains(' ')
        && !is_priced_generic_item_label(&line.left_text, &line.full_text)
        && !short_alpha
    {
        return false;
    }
    if footer_address_like(&line.full_text) {
        return false;
    }
    if looks_like_onsale_marker(&line.left_text) {
        return false;
    }
    if re_multibuy_parenthetical().is_match(&line.left_text) {
        return false;
    }
    if re_short_parenthetical_code().is_match(&line.left_text) && line.left_text.len() < 12 {
        return false;
    }
    true
}

fn has_nearby_quantity_expression_above(all_lines: &[ParsedLine], line_index: usize) -> bool {
    let anchor_y = all_lines[line_index].line_y;
    all_lines
        .iter()
        .enumerate()
        .filter(|(index, candidate)| {
            *index != line_index
                && candidate.line_y < anchor_y
                && anchor_y - candidate.line_y <= MAX_ITEM_DISTANCE
        })
        .max_by(|(_, left), (_, right)| left.line_y.partial_cmp(&right.line_y).unwrap())
        .is_some_and(|(_, candidate)| looks_like_quantity_expression(&candidate.left_text))
}

fn nearest_unpriced_deposit_stub_below(
    all_lines: &[ParsedLine],
    line_index: usize,
    used_line_indices: &[bool],
) -> Option<(usize, f64)> {
    let anchor_y = all_lines[line_index].line_y;
    let nearest_below = all_lines
        .iter()
        .enumerate()
        .filter(|(index, candidate)| {
            *index != line_index
                && candidate.line_y > anchor_y
                && candidate.line_y - anchor_y <= MAX_ITEM_DISTANCE
        })
        .min_by(|(_, left), (_, right)| left.line_y.partial_cmp(&right.line_y).unwrap())?;
    let (index, candidate) = nearest_below;
    if used_line_indices[index]
        || !is_deposit_stub(&candidate.left_text)
        || line_has_trailing_price(&candidate.full_text)
    {
        return None;
    }
    Some((index, candidate.line_y - anchor_y))
}

fn y_center(word: &WordInput) -> f64 {
    (word.bbox.top + word.bbox.bottom) / 2.0
}

fn x_center(word: &WordInput) -> f64 {
    (word.bbox.left + word.bbox.right) / 2.0
}

pub(crate) fn extract_spatial_items(pages: Vec<PageInput>) -> SpatialExtractionOutcome {
    let mut items = Vec::new();
    let mut warnings = Vec::new();
    if pages.is_empty() {
        return SpatialExtractionOutcome { items, warnings };
    }

    let mut all_lines = Vec::new();
    let mut price_candidates = Vec::new();

    for page in &pages {
        for line in &page.lines {
            if line.words.is_empty() {
                continue;
            }
            let full_text = line.text.clone();
            let line_has_price = line_has_trailing_price(&full_text);
            let mut left_words = Vec::new();
            let mut left_y = None;
            for word in &line.words {
                let x = x_center(word);
                if x < ITEM_X_THRESHOLD {
                    let text = word.text.as_str();
                    if text.len() <= 1 || re_digits_dots_only().is_match(text) {
                        continue;
                    }
                    if is_section_header_text(text) && !line_has_price {
                        continue;
                    }
                    left_words.push(text.to_string());
                    if left_y.is_none() {
                        left_y = Some(y_center(word));
                    }
                }
            }
            let line_y = left_y.unwrap_or_else(|| y_center(&line.words[0]));
            let line_index = all_lines.len();
            all_lines.push(ParsedLine {
                line_y,
                full_text: full_text.clone(),
                left_text: left_words.join(" "),
            });
            for word in &line.words {
                if word.confidence < MIN_CONFIDENCE {
                    continue;
                }
                let x = x_center(word);
                if x <= PRICE_X_THRESHOLD {
                    continue;
                }
                if let Some(price_scaled) = is_price_word(&word.text) {
                    if price_scaled > 0 {
                        price_candidates.push(PriceCandidate {
                            price_y: y_center(word),
                            price_scaled,
                            source_line_index: line_index,
                        });
                    }
                }
            }
        }
    }

    let total_line_y = all_lines
        .iter()
        .filter(|line| {
            let upper = line.full_text.to_ascii_uppercase();
            upper.contains("TOTAL") && !upper.contains("SUBTOTAL")
        })
        .map(|line| line.line_y)
        .min_by(|a, b| a.partial_cmp(b).unwrap());

    let mut used_line_indices = vec![false; all_lines.len()];

    for price_candidate in price_candidates {
        let price_y = price_candidate.price_y;
        if let Some(total_y) = total_line_y {
            if price_y > total_y + Y_TOLERANCE {
                continue;
            }
        }
        if all_lines.is_empty() {
            continue;
        }

        let closest_line_index = all_lines
            .iter()
            .enumerate()
            .min_by(|(_, left), (_, right)| {
                (left.line_y - price_y)
                    .abs()
                    .partial_cmp(&(right.line_y - price_y).abs())
                    .unwrap()
            })
            .map(|(index, _)| index);
        let Some(closest_line_index) = closest_line_index else {
            continue;
        };
        let source_line = &all_lines[price_candidate.source_line_index];
        let closest_line = &all_lines[closest_line_index];

        let context_full_text = if source_line.full_text.is_empty() {
            &closest_line.full_text
        } else {
            &source_line.full_text
        };
        let context_left_text = if source_line.left_text.is_empty() {
            &closest_line.left_text
        } else {
            &source_line.left_text
        };
        let full_upper = context_full_text.to_ascii_uppercase();
        let price_line_has_onsale = looks_like_onsale_marker(&full_upper);
        let left_is_header = is_section_header_text(context_left_text)
            && !is_priced_generic_item_label(context_left_text, context_full_text);
        let mut prefer_below = left_is_header
            || is_section_header_text(context_full_text)
            || context_left_text.is_empty();
        if price_line_has_onsale {
            prefer_below = true;
        }

        let mut is_summary = false;
        if let Some(total_y) = total_line_y {
            if price_y > total_y - MAX_ITEM_DISTANCE {
                for candidate in &all_lines {
                    if (candidate.line_y - price_y).abs() > Y_TOLERANCE {
                        continue;
                    }
                    if candidate.line_y > price_y + SPATIAL_FLOAT_EPSILON {
                        continue;
                    }
                    if is_summary_line(&candidate.left_text)
                        || is_summary_line(&candidate.full_text)
                    {
                        is_summary = true;
                        break;
                    }
                }
            }
        }

        if !is_summary {
            let full_text_stripped = closest_line.full_text.trim();
            if is_summary_line(&closest_line.left_text) || is_summary_line(&closest_line.full_text)
            {
                is_summary = true;
            } else if re_standalone_price().is_match(full_text_stripped) {
                let nearest_above = all_lines
                    .iter()
                    .enumerate()
                    .filter(|(_, candidate)| candidate.line_y < closest_line.line_y)
                    .max_by(|(_, left), (_, right)| {
                        left.line_y.partial_cmp(&right.line_y).unwrap()
                    });
                if let Some((_, above)) = nearest_above {
                    if closest_line.line_y - above.line_y <= MAX_ITEM_DISTANCE
                        && (is_summary_line(&above.left_text) || is_summary_line(&above.full_text))
                    {
                        is_summary = true;
                    }
                }
                if !is_summary {
                    if let Some(total_y) = total_line_y {
                        if closest_line.line_y > total_y - MAX_ITEM_DISTANCE {
                            for candidate in &all_lines {
                                if (candidate.line_y - closest_line.line_y).abs()
                                    > MAX_ITEM_DISTANCE
                                {
                                    continue;
                                }
                                if is_summary_line(&candidate.left_text)
                                    || is_summary_line(&candidate.full_text)
                                {
                                    is_summary = true;
                                    break;
                                }
                            }
                        }
                    }
                }
            }
        }

        let mut onsale_target_line_index = None;
        if !is_summary && price_line_has_onsale {
            let anchor_y = source_line.line_y;
            let nearest_above = all_lines
                .iter()
                .enumerate()
                .filter(|(_, candidate)| {
                    candidate.line_y < anchor_y
                        && anchor_y - candidate.line_y <= MAX_ITEM_DISTANCE
                        && is_valid_onsale_target(candidate)
                })
                .max_by(|(_, left), (_, right)| left.line_y.partial_cmp(&right.line_y).unwrap());
            let nearest_below = all_lines
                .iter()
                .enumerate()
                .filter(|(_, candidate)| {
                    candidate.line_y > anchor_y
                        && candidate.line_y - anchor_y <= MAX_ITEM_DISTANCE
                        && is_valid_onsale_target(candidate)
                })
                .min_by(|(_, left), (_, right)| left.line_y.partial_cmp(&right.line_y).unwrap());
            match (nearest_above, nearest_below) {
                (Some((above_index, above)), Some((below_index, below))) => {
                    let above_distance = anchor_y - above.line_y;
                    let below_distance = below.line_y - anchor_y;
                    onsale_target_line_index = Some(if above_distance <= below_distance {
                        above_index
                    } else {
                        below_index
                    });
                }
                (Some((index, _)), None) | (None, Some((index, _))) => {
                    onsale_target_line_index = Some(index);
                }
                (None, None) => {
                    is_summary = true;
                }
            }
        }

        if is_summary {
            continue;
        }

        let line_selection_candidates = all_lines
            .iter()
            .enumerate()
            .map(|(index, line)| {
                crate::spatial::SpatialLineCandidate::new(
                    line.line_y,
                    used_line_indices[index],
                    is_valid_item_line(line, total_line_y),
                    line_has_trailing_price(&line.full_text),
                    looks_like_quantity_expression(&line.left_text),
                )
            })
            .collect::<Vec<_>>();

        let mut found_item = false;
        let mut chosen_line_index = None;
        let mut chosen_distance = f64::INFINITY;
        let mut suppress_fallback_for_ambiguous_code_only_source = false;
        let selection_anchor_y = source_line.line_y;
        let source_line_is_quantity_expression =
            looks_like_quantity_expression(&source_line.left_text);
        let source_line_needs_item_context = lacks_description_context(&source_line.left_text);
        let source_line_repeats_previous_priced_item = source_line_needs_item_context
            && all_lines
                .iter()
                .enumerate()
                .filter(|(_, candidate)| {
                    candidate.line_y < selection_anchor_y
                        && selection_anchor_y - candidate.line_y <= MAX_ITEM_DISTANCE
                        && is_valid_item_line(candidate, total_line_y)
                        && line_has_trailing_price(&candidate.full_text)
                        && trailing_price_scaled(&candidate.full_text)
                            == Some(price_candidate.price_scaled)
                })
                .max_by(|(_, left), (_, right)| left.line_y.partial_cmp(&right.line_y).unwrap())
                .is_some();

        if source_line_is_quantity_expression {
            let source_modifier = parse_quantity_modifier(&source_line.left_text);
            let mut nearest_unpriced_above = None;
            let mut nearest_unpriced_below = None;
            let mut nearest_priced_below_with_deposit_stub = None;
            // Deposit stubs (e.g. "DEPOSIT 1") are normally skipped so a regular
            // quantity expression like "3@$3.49" doesn't pair with a deposit label
            // above it.  But when a quantity expression IS for a deposit (e.g.
            // "2@$0.10 0.20"), the deposit stub immediately above IS the correct
            // target.  Track the closest unused deposit stub within Y_TOLERANCE so
            // we can fall back to it when no regular item is found above.
            let mut nearest_deposit_stub_above_within_tolerance: Option<(usize, f64)> = None;

            for (index, candidate) in all_lines.iter().enumerate() {
                if used_line_indices[index] || !is_valid_item_line(candidate, total_line_y) {
                    continue;
                }

                let distance = (candidate.line_y - selection_anchor_y).abs();
                if distance > MAX_ITEM_DISTANCE + SPATIAL_FLOAT_EPSILON {
                    continue;
                }

                let candidate_has_trailing_price = line_has_trailing_price(&candidate.full_text);
                if candidate_has_trailing_price {
                    if candidate.line_y > selection_anchor_y
                        && nearest_unpriced_deposit_stub_below(
                            &all_lines,
                            index,
                            &used_line_indices,
                        )
                        .is_some()
                    {
                        match nearest_priced_below_with_deposit_stub {
                            Some((_, current_distance)) if distance >= current_distance => {}
                            _ => nearest_priced_below_with_deposit_stub = Some((index, distance)),
                        }
                    }
                    continue;
                }

                if is_deposit_stub(&candidate.left_text) {
                    // Track closest deposit stub above within Y_TOLERANCE as a
                    // fallback for deposit-quantity expressions.
                    if candidate.line_y < selection_anchor_y
                        && distance <= Y_TOLERANCE + SPATIAL_FLOAT_EPSILON
                    {
                        match nearest_deposit_stub_above_within_tolerance {
                            Some((_, current_distance)) if distance >= current_distance => {}
                            _ => {
                                nearest_deposit_stub_above_within_tolerance =
                                    Some((index, distance))
                            }
                        }
                    }
                    continue;
                }

                if candidate.line_y < selection_anchor_y {
                    match nearest_unpriced_above {
                        Some((_, current_distance)) if distance >= current_distance => {}
                        _ => nearest_unpriced_above = Some((index, distance)),
                    }
                } else if candidate.line_y > selection_anchor_y {
                    match nearest_unpriced_below {
                        Some((_, current_distance)) if distance >= current_distance => {}
                        _ => nearest_unpriced_below = Some((index, distance)),
                    }
                }
            }

            chosen_line_index = match (
                nearest_unpriced_above,
                nearest_unpriced_below,
                source_modifier,
            ) {
                (Some((index, distance)), Some(_), true) => {
                    chosen_distance = distance;
                    Some(index)
                }
                (
                    Some((above_index, above_distance)),
                    Some((below_index, below_distance)),
                    false,
                ) => {
                    if above_distance <= below_distance {
                        chosen_distance = above_distance;
                        Some(above_index)
                    } else {
                        chosen_distance = below_distance;
                        Some(below_index)
                    }
                }
                (Some((index, distance)), None, _) => {
                    chosen_distance = distance;
                    Some(index)
                }
                // No regular item above: prefer a deposit stub within Y_TOLERANCE
                // over a non-deposit item below, so "2@$0.10" pairs with "DEPOSIT 1"
                // rather than the next real item below.
                (None, Some((below_index, below_distance)), _) => {
                    if let Some((stub_index, stub_distance)) =
                        nearest_deposit_stub_above_within_tolerance
                    {
                        chosen_distance = stub_distance;
                        Some(stub_index)
                    } else {
                        chosen_distance = below_distance;
                        Some(below_index)
                    }
                }
                (None, None, _) => nearest_priced_below_with_deposit_stub
                    .or(nearest_deposit_stub_above_within_tolerance)
                    .map(|(index, distance)| {
                        chosen_distance = distance;
                        index
                    }),
            };
        }

        if !prefer_below && source_line_is_quantity_expression {
            let mut nearest_same_row_above = None;
            let mut nearest_same_row_below = None;

            for (index, candidate) in all_lines.iter().enumerate() {
                if used_line_indices[index] || !is_valid_item_line(candidate, total_line_y) {
                    continue;
                }
                let distance = (candidate.line_y - selection_anchor_y).abs();
                if distance > Y_TOLERANCE + SPATIAL_FLOAT_EPSILON {
                    continue;
                }
                if candidate.line_y < selection_anchor_y {
                    match nearest_same_row_above {
                        Some(current_distance) if distance >= current_distance => {}
                        _ => nearest_same_row_above = Some(distance),
                    }
                } else if candidate.line_y > selection_anchor_y {
                    match nearest_same_row_below {
                        Some(current_distance) if distance >= current_distance => {}
                        _ => nearest_same_row_below = Some(distance),
                    }
                }
            }

            if nearest_same_row_below.is_some() && nearest_same_row_above.is_none() {
                prefer_below = true;
            }
        }

        let source_distance = (source_line.line_y - price_y).abs();
        let shifted_deposit_target = if source_distance <= Y_TOLERANCE
            && is_valid_item_line(source_line, total_line_y)
            && !looks_like_quantity_expression(&source_line.left_text)
            && has_nearby_quantity_expression_above(&all_lines, price_candidate.source_line_index)
        {
            nearest_unpriced_deposit_stub_below(
                &all_lines,
                price_candidate.source_line_index,
                &used_line_indices,
            )
        } else {
            None
        };

        if onsale_target_line_index.is_none()
            && !used_line_indices[price_candidate.source_line_index]
        {
            if shifted_deposit_target.is_none()
                && trailing_price_scaled(&source_line.full_text) == Some(price_candidate.price_scaled)
                && is_valid_item_line(source_line, total_line_y)
                && !looks_like_quantity_expression(&source_line.left_text)
            {
                chosen_line_index = Some(price_candidate.source_line_index);
                chosen_distance = source_distance;
            } else if let Some((index, distance)) = shifted_deposit_target {
                chosen_line_index = Some(index);
                chosen_distance = distance;
            } else if source_distance <= Y_TOLERANCE
                && is_valid_item_line(source_line, total_line_y)
                && !looks_like_quantity_expression(&source_line.left_text)
            {
                chosen_line_index = Some(price_candidate.source_line_index);
                chosen_distance = source_distance;
            }
        }

        if chosen_line_index.is_none() {
            if let Some(index) = onsale_target_line_index {
                if !used_line_indices[index] {
                    chosen_line_index = Some(index);
                    chosen_distance = (all_lines[index].line_y - price_y).abs();
                }
            }
        }

        if chosen_line_index.is_none() {
            if let Some((index, distance)) = crate::spatial::select_spatial_item_line(
                selection_anchor_y,
                Y_TOLERANCE,
                MAX_ITEM_DISTANCE,
                prefer_below,
                price_line_has_onsale,
                line_selection_candidates,
            ) {
                let selected_line = &all_lines[index];
                let selected_line_is_next_priced_row = source_line_needs_item_context
                    && selected_line.line_y > price_y + SPATIAL_FLOAT_EPSILON
                    && line_has_trailing_price(&selected_line.full_text);
                if selected_line_is_next_priced_row {
                    suppress_fallback_for_ambiguous_code_only_source = true;
                } else {
                    chosen_line_index = Some(index);
                    chosen_distance = distance;
                }
            }
        }

        if let Some(index) = chosen_line_index {
            let direct_match_tolerance = if source_line_is_quantity_expression || prefer_below {
                MAX_ITEM_DISTANCE + SPATIAL_FLOAT_EPSILON
            } else {
                Y_TOLERANCE + SPATIAL_FLOAT_EPSILON
            };
            if chosen_distance <= direct_match_tolerance {
                let description = clean_description(&all_lines[index].left_text);
                if description.len() > 2 {
                    used_line_indices[index] = true;
                    items.push(SpatialExtractedItem {
                        description,
                        price_scaled: price_candidate.price_scaled,
                    });
                    found_item = true;
                }
            }
        }

        if !found_item
            && shifted_deposit_target.is_none()
            && !used_line_indices[price_candidate.source_line_index]
            && trailing_price_scaled(&source_line.full_text) == Some(price_candidate.price_scaled)
            && is_valid_item_line(source_line, total_line_y)
            && !looks_like_quantity_expression(&source_line.left_text)
        {
            let description = clean_description(&source_line.left_text);
            if description.len() > 2 {
                used_line_indices[price_candidate.source_line_index] = true;
                items.push(SpatialExtractedItem {
                    description,
                    price_scaled: price_candidate.price_scaled,
                });
                found_item = true;
            }
        }

        if !found_item && !suppress_fallback_for_ambiguous_code_only_source {
            if source_line_repeats_previous_priced_item {
                continue;
            }
            let mut lines_above = all_lines
                .iter()
                .enumerate()
                .filter(|(_, line)| {
                    line.line_y < price_y - Y_TOLERANCE
                        && (price_y - line.line_y) <= MAX_ITEM_DISTANCE
                })
                .collect::<Vec<_>>();
            lines_above
                .sort_by(|(_, left), (_, right)| right.line_y.partial_cmp(&left.line_y).unwrap());

            for (index, line) in lines_above.into_iter().take(5) {
                if used_line_indices[index] {
                    continue;
                }
                if price_line_has_onsale && line_has_trailing_price(&line.full_text) {
                    continue;
                }
                if line.left_text.len() < 3 {
                    continue;
                }
                if is_summary_line(&line.left_text) || is_summary_line(&line.full_text) {
                    continue;
                }
                if re_weight_info().is_match(&line.full_text.to_ascii_lowercase()) {
                    continue;
                }
                if re_w_dollar().is_match(&line.full_text) {
                    continue;
                }
                if re_standalone_price().is_match(line.full_text.trim()) {
                    continue;
                }
                let left_is_header = is_section_header_text(&line.left_text)
                    && !is_priced_generic_item_label(&line.left_text, &line.full_text);
                if left_is_header || is_section_header_text(&line.full_text) {
                    continue;
                }
                let left_text_for_ratio = strip_leading_receipt_codes(&line.left_text);
                if left_text_for_ratio.is_empty() {
                    continue;
                }
                if alpha_ratio(&left_text_for_ratio) < 0.4 {
                    continue;
                }
                let description = clean_description(&line.left_text);
                if description.len() > 2 {
                    used_line_indices[index] = true;
                    items.push(SpatialExtractedItem {
                        description,
                        price_scaled: price_candidate.price_scaled,
                    });
                    found_item = true;
                    break;
                }
            }
        }

        if !found_item {
            let mut context_text = source_line.full_text.trim().to_string();
            if context_text.is_empty() {
                context_text = closest_line.full_text.trim().to_string();
            }
            if context_text.len() > 80 {
                context_text.truncate(80);
            }
            let mut message = format!(
                "maybe missed item near price {}",
                format_scaled_currency(price_candidate.price_scaled)
            );
            if !context_text.is_empty() {
                message.push_str(&format!(" (context: \"{}\")", context_text));
            }
            warnings.push(SpatialParserWarning {
                message,
                after_item_index: if items.is_empty() {
                    None
                } else {
                    Some(items.len() - 1)
                },
            });
        }
    }

    SpatialExtractionOutcome { items, warnings }
}

#[cfg(test)]
mod tests {
    use super::{extract_spatial_items, BboxInput, LineInput, PageInput, WordInput};

    fn word(text: &str, left: f64, top: f64, right: f64, bottom: f64) -> WordInput {
        WordInput {
            text: text.to_string(),
            bbox: BboxInput {
                left,
                top,
                right,
                bottom,
            },
            confidence: 0.99,
        }
    }

    #[test]
    fn keeps_short_produce_name_alignment() {
        let page = PageInput {
            lines: vec![
                LineInput {
                    text: "&& 02-Vegetable".to_string(),
                    words: vec![word("&& 02-Vegetable", 0.15, 0.355, 0.30, 0.364)],
                },
                LineInput {
                    text: "Napa".to_string(),
                    words: vec![word("Napa", 0.06, 0.365, 0.09, 0.372)],
                },
                LineInput {
                    text: "2.46 1b @ $1.29/1b 3.17".to_string(),
                    words: vec![
                        word("2.46 1b @ $1.29/1b", 0.20, 0.378, 0.27, 0.386),
                        word("3.17", 0.89, 0.377, 0.92, 0.384),
                    ],
                },
                LineInput {
                    text: "Soybean Sprout".to_string(),
                    words: vec![word("Soybean Sprout", 0.12, 0.388, 0.24, 0.395)],
                },
                LineInput {
                    text: "0.65 1b @ $1.58/1b 1.03".to_string(),
                    words: vec![
                        word("0.65 1b @ $1.58/1b", 0.21, 0.401, 0.28, 0.409),
                        word("1.03", 0.89, 0.400, 0.92, 0.407),
                    ],
                },
            ],
        };

        let outcome = extract_spatial_items(vec![page]);
        let observed = outcome
            .items
            .into_iter()
            .map(|item| (item.description, item.price_scaled))
            .collect::<Vec<_>>();

        assert!(observed.contains(&("Napa".to_string(), 31_700)));
        assert!(observed.contains(&("Soybean Sprout".to_string(), 10_300)));
    }

    #[test]
    fn prefers_item_above_onsale_price() {
        let page = PageInput {
            lines: vec![
                LineInput {
                    text: "*S & B Wasabi".to_string(),
                    words: vec![word("*S & B Wasabi", 0.08, 0.100, 0.260, 0.112)],
                },
                LineInput {
                    text: "(E)ON SALE 1.98".to_string(),
                    words: vec![
                        word("(E)ON SALE", 0.09, 0.120, 0.210, 0.132),
                        word("1.98", 0.88, 0.120, 0.93, 0.132),
                    ],
                },
                LineInput {
                    text: "2 @ $0.99 4.59".to_string(),
                    words: vec![
                        word("2 @ $0.99", 0.22, 0.140, 0.320, 0.152),
                        word("4.59", 0.88, 0.140, 0.93, 0.152),
                    ],
                },
                LineInput {
                    text: "Hot Kid Honey Flavour Bal".to_string(),
                    words: vec![word("Hot Kid Honey Flavour Bal", 0.08, 0.160, 0.360, 0.172)],
                },
                LineInput {
                    text: "TOTAL 6.57".to_string(),
                    words: vec![
                        word("TOTAL", 0.09, 0.500, 0.180, 0.512),
                        word("6.57", 0.88, 0.500, 0.93, 0.512),
                    ],
                },
            ],
        };

        let outcome = extract_spatial_items(vec![page]);
        let observed = outcome
            .items
            .into_iter()
            .map(|item| (item.description, item.price_scaled))
            .collect::<Vec<_>>();

        assert_eq!(
            observed,
            vec![
                ("S & B Wasabi".to_string(), 19_800),
                ("Hot Kid Honey Flavour Bal".to_string(), 45_900),
            ]
        );
    }

    #[test]
    fn quantity_price_row_with_ea_suffix_uses_item_above() {
        let page = PageInput {
            lines: vec![
                LineInput {
                    text: "FF SHEPHERDS PURSE FILLING".to_string(),
                    words: vec![word("FF SHEPHERDS PURSE FILLING", 0.05, 0.700, 0.40, 0.712)],
                },
                LineInput {
                    text: "2 @ $3.49ea. W $6.98".to_string(),
                    words: vec![
                        word("2 @ $3.49ea.", 0.07, 0.723, 0.23, 0.735),
                        word("W $6.98", 0.88, 0.723, 0.95, 0.735),
                    ],
                },
                LineInput {
                    text: "TOTAL 6.98".to_string(),
                    words: vec![
                        word("TOTAL", 0.10, 0.900, 0.18, 0.912),
                        word("6.98", 0.88, 0.900, 0.93, 0.912),
                    ],
                },
            ],
        };

        let outcome = extract_spatial_items(vec![page]);
        let observed = outcome
            .items
            .into_iter()
            .map(|item| (item.description, item.price_scaled))
            .collect::<Vec<_>>();

        assert_eq!(
            observed,
            vec![("FF SHEPHERDS PURSE FILLING".to_string(), 69_800)]
        );
    }

    #[test]
    fn skips_receipt_metadata_when_quantity_row_needs_item_context() {
        let page = PageInput {
            lines: vec![
                LineInput {
                    text: "WS# P6 Cashier6".to_string(),
                    words: vec![word("WS# P6 Cashier6", 0.05, 0.100, 0.22, 0.112)],
                },
                LineInput {
                    text: "*S & B Wasabi".to_string(),
                    words: vec![word("*S & B Wasabi", 0.08, 0.140, 0.260, 0.152)],
                },
                LineInput {
                    text: "(E)ON SALE 1.98".to_string(),
                    words: vec![
                        word("(E)ON SALE", 0.09, 0.160, 0.210, 0.172),
                        word("1.98", 0.88, 0.160, 0.93, 0.172),
                    ],
                },
                LineInput {
                    text: "2 @ $0.99 4.59".to_string(),
                    words: vec![
                        word("2 @ $0.99", 0.22, 0.180, 0.320, 0.192),
                        word("4.59", 0.88, 0.180, 0.93, 0.192),
                    ],
                },
                LineInput {
                    text: "Hot Kid Honey Flavour Bal".to_string(),
                    words: vec![word("Hot Kid Honey Flavour Bal", 0.08, 0.200, 0.360, 0.212)],
                },
                LineInput {
                    text: "TOTAL 6.57".to_string(),
                    words: vec![
                        word("TOTAL", 0.09, 0.500, 0.180, 0.512),
                        word("6.57", 0.88, 0.500, 0.93, 0.512),
                    ],
                },
            ],
        };

        let outcome = extract_spatial_items(vec![page]);
        let observed = outcome
            .items
            .into_iter()
            .map(|item| (item.description, item.price_scaled))
            .collect::<Vec<_>>();

        assert_eq!(
            observed,
            vec![
                ("S & B Wasabi".to_string(), 19_800),
                ("Hot Kid Honey Flavour Bal".to_string(), 45_900),
            ]
        );
    }
}
