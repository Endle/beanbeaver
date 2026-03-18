use regex::Regex;
use std::sync::OnceLock;

const GENERIC_PRICED_ITEM_LABELS: &[&str] = &["MEAT", "BAKERY"];
const SECTION_HEADERS: &[&str] = &[
    "MEAT", "SEAFOOD", "PRODUCE", "DELI", "GROCERY", "BAKERY", "FROZEN",
];

#[derive(Clone, Debug)]
pub(crate) struct QuantityModifier {
    pub(crate) quantity: i32,
    pub(crate) unit_price_scaled: Option<i64>,
    pub(crate) weight: Option<String>,
    pub(crate) deal_price_scaled: Option<i64>,
    pub(crate) pattern_type: String,
    pub(crate) raw_line: String,
}

fn re_spaced_decimal() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(\d)\.\s+(\d{2}\b)").unwrap())
}

fn re_section_header_with_aisle() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[^A-Z0-9]*\d{1,2}\s*[-:]\s*[A-Z]{3,}$").unwrap())
}

fn re_section_aisle_prefix() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[^A-Z0-9]*\d{1,2}\s*[-:]").unwrap())
}

fn re_summary_patterns() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)^(?:SUB\s*TOTAL|SUBTOTAL|TOTAL|HST|GST|PST|TAX|MASTER(?:CARD)?|VISA|DEBIT|CREDIT|POINTS|CASH|CHANGE|BALANCE|APPROVED|CARD|TERMINAL|MEMBER)\b",
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

fn re_trailing_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\d+\.\d{2}\s*[HhTtJj]?\s*$").unwrap())
}

fn re_onsale_marker() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^(?:[A-Z0-9]{0,3})?ONSAL[E]?$").unwrap())
}

fn re_count_at_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^(\d+)\s*@\s*\$?(-?\d+\.\d{2})").unwrap())
}

fn re_weight_at_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^(\d+\.?\d*)\s*(?:lb|lk|kg|k[g9]|1b|1k)\s*@").unwrap())
}

fn re_multi_for_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^\(?(\d+)\s*/\s*for\s+\$?(\d+\.\d{2})\)?").unwrap())
}

fn re_negative_count_at_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^\d+\s*@\s*\$?-?\d+\.\d{2}\s*$").unwrap())
}

fn re_quantity_for() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^\d+\s*/\s*for\b").unwrap())
}

fn re_quantity_at_per_for() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^\d+\s*@\s*\d+\s*/\s*\$?\d+\.\d{2}\b").unwrap())
}

fn re_paren_for_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\(\d+\s*/\s*for\s+\$[\d.]+\)").unwrap())
}

fn re_prefixed_paren_for_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^\([^)]+\)\s+\d+\s*/\s*for\b").unwrap())
}

fn re_quantity_prefix() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\(\d+\)\s*").unwrap())
}

fn re_long_leading_sku() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\d{6,}\s*").unwrap())
}

fn re_alpha_tokens() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[A-Z]+").unwrap())
}

fn re_remove_sale_marker() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\(SALE\)\s*").unwrap())
}

fn re_remove_hed() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\(HED[^)]*\)\s*").unwrap())
}

fn re_remove_hhed() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\(HHED[^)]*\)\s*").unwrap())
}

fn re_remove_at_price_ratio() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"@?\d+/[A-Za-z]?\$?\d+\.\d{2}").unwrap())
}

fn re_remove_price_ratio() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\d+/\$?\d+\.\d{2}").unwrap())
}

fn re_remove_price_per_unit() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\$\d+\.\d+/\w+").unwrap())
}

fn re_remove_standalone_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\$\d+\.\d{2}").unwrap())
}

fn re_remove_garbled_ea() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\d+s\d+\.\d+ea").unwrap())
}

fn re_remove_cahrd() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\bCAHRD\b").unwrap())
}

fn re_remove_hed_word() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\bHED\b").unwrap())
}

fn re_trim_prefix_special() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[^A-Za-z0-9]+").unwrap())
}

fn re_trim_suffix_special() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[^A-Za-z0-9)]+$").unwrap())
}

fn re_collapse_whitespace() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\s+").unwrap())
}

fn re_price_word() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\$?(\d+\.\d{2})$").unwrap())
}

fn parse_decimal_scaled(value: &str, scale: i64) -> Option<i64> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return None;
    }

    let negative = trimmed.starts_with('-');
    let unsigned = trimmed.trim_start_matches('-');
    let mut parts = unsigned.splitn(2, '.');
    let whole = parts.next()?.parse::<i64>().ok()?;
    let frac_raw = parts.next().unwrap_or("");
    let digits = scale.to_string().len() - 1;

    let mut frac_digits = frac_raw
        .chars()
        .filter(|ch| ch.is_ascii_digit())
        .collect::<Vec<_>>();
    while frac_digits.len() < digits + 1 {
        frac_digits.push('0');
    }

    let round_up = frac_digits[digits] >= '5';
    let base_frac = frac_digits[..digits]
        .iter()
        .collect::<String>()
        .parse::<i64>()
        .ok()?;

    let mut scaled = whole.checked_mul(scale)? + base_frac;
    if round_up {
        scaled += 1;
    }
    Some(if negative { -scaled } else { scaled })
}

pub(crate) fn parse_scaled_4(value: &str) -> Option<i64> {
    parse_decimal_scaled(value, 10_000)
}

fn format_scaled_decimal(value: i64, scale: i64) -> String {
    let sign = if value < 0 { "-" } else { "" };
    let abs = value.abs();
    let digits = scale.to_string().len() - 1;
    let whole = abs / scale;
    let frac = abs % scale;
    let frac_text = format!("{frac:0digits$}");
    let frac_trimmed = frac_text.trim_end_matches('0');
    if frac_trimmed.is_empty() {
        format!("{sign}{whole}")
    } else {
        format!("{sign}{whole}.{frac_trimmed}")
    }
}

fn collapse_internal_whitespace(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub(crate) fn normalize_decimal_spacing(text: &str) -> String {
    re_spaced_decimal().replace_all(text, "$1.$2").to_string()
}

pub(crate) fn is_section_header_text(text: &str) -> bool {
    if text.trim().is_empty() {
        return false;
    }
    let normalized = collapse_internal_whitespace(text.trim()).to_ascii_uppercase();
    if SECTION_HEADERS.iter().any(|header| *header == normalized) {
        return true;
    }
    if re_section_header_with_aisle().is_match(&normalized) {
        return true;
    }
    if re_section_aisle_prefix().is_match(&normalized) {
        let tokens = re_alpha_tokens()
            .find_iter(&normalized)
            .map(|m| m.as_str())
            .collect::<Vec<_>>();
        return tokens
            .iter()
            .any(|token| SECTION_HEADERS.iter().any(|header| header == token));
    }
    false
}

pub(crate) fn strip_leading_receipt_codes(text: &str) -> String {
    let cleaned = re_quantity_prefix().replace(text.trim(), "");
    re_long_leading_sku()
        .replace(cleaned.as_ref(), "")
        .trim()
        .to_string()
}

pub(crate) fn looks_like_summary_line(text: &str) -> bool {
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
    if upper.contains("HST")
        || upper.contains("GST")
        || upper.contains("PST")
        || upper.contains("TAX")
    {
        return true;
    }
    upper.starts_with("H=")
        && ["HST", "GST", "PST", "TAX"]
            .iter()
            .any(|tag| upper.contains(tag))
}

pub(crate) fn looks_like_receipt_metadata_line(text: &str) -> bool {
    !text.trim().is_empty() && re_receipt_metadata_patterns().is_match(text.trim())
}

pub(crate) fn line_has_trailing_price(text: &str) -> bool {
    if text.trim().is_empty() {
        return false;
    }
    let normalized = normalize_decimal_spacing(text.trim());
    re_trailing_price().is_match(&normalized)
}

pub(crate) fn looks_like_onsale_marker(text: &str) -> bool {
    if text.trim().is_empty() {
        return false;
    }
    let normalized = normalize_decimal_spacing(&text.trim().to_ascii_uppercase());
    let trimmed = re_trailing_price()
        .replace(&normalized, "")
        .trim()
        .to_string();
    let compact = trimmed
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .collect::<String>();
    re_onsale_marker().is_match(&compact)
}

pub(crate) fn is_priced_generic_item_label(left_text: &str, full_text: &str) -> bool {
    line_has_trailing_price(full_text)
        && GENERIC_PRICED_ITEM_LABELS
            .iter()
            .any(|label| *label == left_text.trim().to_ascii_uppercase())
}

pub(crate) fn parse_quantity_modifier(line: &str) -> Option<QuantityModifier> {
    let normalized = normalize_decimal_spacing(line.trim());

    if let Some(captures) = re_count_at_price().captures(&normalized) {
        return Some(QuantityModifier {
            quantity: captures.get(1)?.as_str().parse::<i32>().ok()?,
            unit_price_scaled: parse_decimal_scaled(captures.get(2)?.as_str(), 10_000),
            weight: None,
            deal_price_scaled: None,
            pattern_type: "count_at_price".to_string(),
            raw_line: normalized,
        });
    }

    if let Some(captures) = re_weight_at_price().captures(&normalized) {
        return Some(QuantityModifier {
            quantity: 1,
            unit_price_scaled: None,
            weight: Some(captures.get(1)?.as_str().to_string()),
            deal_price_scaled: None,
            pattern_type: "weight_at_price".to_string(),
            raw_line: normalized,
        });
    }

    if let Some(captures) = re_multi_for_price().captures(&normalized) {
        let quantity = captures.get(1)?.as_str().parse::<i32>().ok()?;
        let deal_price_scaled = parse_decimal_scaled(captures.get(2)?.as_str(), 10_000)?;
        let numerator = (deal_price_scaled as i128) + ((quantity as i128) / 2);
        let unit_price_scaled = (numerator / (quantity as i128)) as i64;
        return Some(QuantityModifier {
            quantity,
            unit_price_scaled: Some(unit_price_scaled),
            weight: None,
            deal_price_scaled: Some(deal_price_scaled),
            pattern_type: "multi_for_price".to_string(),
            raw_line: normalized,
        });
    }

    None
}

pub(crate) fn validate_quantity_price(
    total_price_scaled: i64,
    modifier: &QuantityModifier,
    tolerance_scaled: i64,
) -> bool {
    match modifier.pattern_type.as_str() {
        "count_at_price" => modifier.unit_price_scaled.is_some_and(|unit_price| {
            (modifier.quantity as i64 * unit_price - total_price_scaled).abs() <= tolerance_scaled
        }),
        "multi_for_price" => modifier
            .deal_price_scaled
            .is_some_and(|deal_price| (deal_price - total_price_scaled).abs() <= tolerance_scaled),
        "weight_at_price" => true,
        _ => false,
    }
}

pub(crate) fn looks_like_quantity_expression(text: &str) -> bool {
    let normalized = normalize_decimal_spacing(text.trim());
    if normalized.is_empty() {
        return false;
    }

    if parse_quantity_modifier(&normalized).is_some() {
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
        let compact = upper
            .chars()
            .filter(|ch| !ch.is_ascii_whitespace())
            .collect::<String>();
        let alpha_count = compact
            .chars()
            .filter(|ch| ch.is_ascii_alphabetic())
            .count();
        let digit_count = compact.chars().filter(|ch| ch.is_ascii_digit()).count();
        if digit_count >= 3 && alpha_count <= 4 {
            return true;
        }
    }

    re_negative_count_at_price().is_match(&normalized)
        || re_quantity_for().is_match(&normalized)
        || re_quantity_at_per_for().is_match(&normalized)
        || re_paren_for_price().is_match(&normalized)
        || re_prefixed_paren_for_price().is_match(&normalized)
}

pub(crate) fn extract_price_word(text: &str) -> Option<String> {
    let normalized = normalize_decimal_spacing(text.trim());
    let normalized = normalized
        .trim_start_matches(|ch: char| ch == 'W' || ch == 'w')
        .trim_start()
        .to_string();
    let captures = re_price_word().captures(&normalized)?;
    Some(captures.get(1)?.as_str().to_string())
}

pub(crate) fn clean_description(description: &str) -> String {
    let mut cleaned = description.to_string();
    cleaned = re_quantity_prefix().replace(&cleaned, "").into_owned();
    cleaned = re_remove_sale_marker()
        .replace_all(&cleaned, "")
        .into_owned();
    cleaned = re_remove_hed().replace_all(&cleaned, "").into_owned();
    cleaned = re_remove_hhed().replace_all(&cleaned, "").into_owned();
    cleaned = re_remove_at_price_ratio()
        .replace_all(&cleaned, "")
        .into_owned();
    cleaned = re_remove_price_ratio()
        .replace_all(&cleaned, "")
        .into_owned();
    cleaned = re_remove_price_per_unit()
        .replace_all(&cleaned, "")
        .into_owned();
    cleaned = re_remove_standalone_price()
        .replace_all(&cleaned, "")
        .into_owned();
    cleaned = re_remove_garbled_ea()
        .replace_all(&cleaned, "")
        .into_owned();
    cleaned = re_long_leading_sku().replace(&cleaned, "").into_owned();
    cleaned = re_remove_cahrd().replace_all(&cleaned, "").into_owned();
    cleaned = re_remove_hed_word().replace_all(&cleaned, "").into_owned();
    cleaned = re_trim_prefix_special().replace(&cleaned, "").into_owned();
    cleaned = re_trim_suffix_special().replace(&cleaned, "").into_owned();
    re_collapse_whitespace()
        .replace_all(&cleaned, " ")
        .trim()
        .to_string()
}

pub(crate) fn format_scaled_4(value: i64) -> String {
    format_scaled_decimal(value, 10_000)
}
