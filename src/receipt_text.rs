use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

#[derive(Clone, Debug)]
pub(crate) struct ParsedTextItem {
    pub(crate) description: String,
    pub(crate) category_source: String,
    pub(crate) price_cents: i64,
    pub(crate) quantity: i32,
}

#[derive(Clone, Debug)]
pub(crate) struct TextParserWarning {
    pub(crate) message: String,
    pub(crate) after_item_index: Option<usize>,
}

#[derive(Clone, Debug)]
struct MalformedTrailingPriceCandidate {
    description: String,
    category_source: String,
    observed_token: String,
    observed_fraction: String,
    whole_dollars: i64,
    context: String,
}

#[derive(Clone, Debug)]
struct CandidatePriceOption {
    price_cents: i64,
    score: usize,
}

#[derive(Clone, Debug)]
enum DeferredTextOutcome {
    Item(ParsedTextItem),
    Warning(String),
    Malformed(MalformedTrailingPriceCandidate),
}

#[derive(Clone, Debug)]
struct QuantityModifier {
    quantity: i32,
    unit_price_cents: Option<i64>,
    weight_text: Option<String>,
    deal_price_cents: Option<i64>,
    pattern_type: QuantityPatternType,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum QuantityPatternType {
    CountAtPrice,
    WeightAtPrice,
    MultiForPrice,
}

fn re_skip_patterns() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)TOTAL|SUBTOTAL|SUB\s+TOTAL|TOTALS?\s+ON|^TAX$|^HST|^GST|^PST|AFTER\s+TAX|\d+%$|^CASH\b|^CREDIT\b|^DEBIT\b|^CHANGE\b|^BALANCE|^VISA\b|^MASTERCARD\b|^AMEX\b|^APPROVED\b|^ACTIVATED\b|^PC\s+\d|^ACCT:|^REFERENCE|THANK YOU|WELCOME|RECEIPT|TRANSACTION|^POINTS\b|^REWARDS\b|^EARNED\b|^SAVED$|^YOU SAVED|^CARD|AUTH|REF\s*#|SLIP\s*#|^TILL|CASHIER|\bSTORE\b|^PHONE|ADDRESS|SIGNATURE|Merchant|^QTY$|^UNIT$|^SAV$|ITEM\s+COUNT|NUMBER\s+OF\s+ITEMS|XXXX+|^CAD|VERIFIED|^PIN$|CUSTOMER\s+COPY|COPY$|Optimum|Redeemed",
        )
        .unwrap()
    })
}

fn re_total_word() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\bTOTAL\b").unwrap())
}

fn re_digits_only() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\d+$").unwrap())
}

fn re_parenthetical_only() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\([^)]*\)?$").unwrap())
}

fn re_trailing_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(\d+\.\d{2})(-?)\s*[HhTtJj]?\s*$").unwrap())
}

fn re_trailing_total_presence() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\s+\d+\.\d{2}\s*[HhTtJj]?\s*$").unwrap())
}

fn re_tail_token() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"([0-9A-Za-z]\.[0-9A-Za-z]{2,3}[HhTtJj]?)\s*$").unwrap())
}

fn re_compact_space() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\s+").unwrap())
}

fn re_reg_price_marker() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?:^|[^A-Z0-9])[0-9OI]?REG\$?\d+\.\d{2}").unwrap())
}

fn re_find_prices() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(\d+\.\d{2})").unwrap())
}

fn re_compact_promo_ghost() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Z]{1,5}\$?\d+\.\d{2}[HHTTJJ]?$").unwrap())
}

fn re_standalone_price_line() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\$?\d+\.\d{2}\s*[HhTtJj]?\s*$").unwrap())
}

fn re_long_digits_line() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\d{8,}\s*$").unwrap())
}

fn re_weak_parenthetical() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\([^)]{1,12}\)$").unwrap())
}

fn re_weak_measure() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^\d+(?:\.\d+)?\s*(?:KG|G|LB|L|ML|OZ)$").unwrap())
}

fn re_malformed_price_marker() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\d+\s*@\s*$").unwrap())
}

fn re_onsale_parenthetical() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^\([#\w]*\)\s*<?\s*ON\s*SALE").unwrap())
}

fn re_price_info_line() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\$\d+\.\d{2}").unwrap())
}

fn re_parenthetical_closed() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\([^)]*\)$").unwrap())
}

fn re_parenthetical_multibuy() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\(\d+\s*/\s*for\s+\$[\d.]+\)").unwrap())
}

fn re_malformed_ocr_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(\d+[Il]\.\d{2}|\d+\.[Il]\d|\d+\.\d[Il])\s*[HhTtJj]?\s*$").unwrap()
    })
}

fn re_trailing_noisy_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(\d+)\.(\d{3})\s*[HhTtJj]?\s*$").unwrap())
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

fn re_negative_unit_qty() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^\d+\s*@\s*\$?-?\d+\.\d{2}\s*$").unwrap())
}

fn re_compact_offer_fragment() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^\d+\s*@\s*\d+\s*/\s*\$?\d+\.\d{2}\b").unwrap())
}

fn re_parenthetical_offer_prefix() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^\([^)]+\)\s+\d+\s*/\s*for\b").unwrap())
}

fn re_section_header_with_aisle() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[^A-Z0-9]*\d{1,2}\s*[-:]\s*[A-Z]{3,}$").unwrap())
}

fn re_section_aisle_prefix() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[^A-Z0-9]*\d{1,2}\s*[-:]").unwrap())
}

fn re_ascii_words() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[A-Z]+").unwrap())
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

fn re_tax_tokens() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\b(HST|GST|PST|TAX)\b").unwrap())
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

fn parse_cents(token: &str) -> Option<i64> {
    let trimmed = token.trim();
    let (whole, frac) = trimmed.split_once('.')?;
    if whole.is_empty() || frac.len() != 2 {
        return None;
    }
    if !whole.chars().all(|ch| ch.is_ascii_digit()) || !frac.chars().all(|ch| ch.is_ascii_digit()) {
        return None;
    }
    let dollars = whole.parse::<i64>().ok()?;
    let cents = frac.parse::<i64>().ok()?;
    Some(dollars * 100 + cents)
}

fn format_cents(value: i64) -> String {
    let abs_value = value.abs();
    let dollars = abs_value / 100;
    let cents = abs_value % 100;
    if value < 0 {
        format!("-{dollars}.{cents:02}")
    } else {
        format!("{dollars}.{cents:02}")
    }
}

fn alpha_ratio(value: &str) -> f64 {
    if value.is_empty() {
        return 0.0;
    }
    let alpha_count = value.chars().filter(|ch| ch.is_ascii_alphabetic()).count();
    alpha_count as f64 / value.len() as f64
}

fn strip_leading_receipt_codes(text: &str) -> String {
    let trimmed = text.trim();
    let trimmed = Regex::new(r"^\(\d+\)\s*").unwrap().replace(trimmed, "");
    let trimmed = Regex::new(r"^\d{6,}\s*")
        .unwrap()
        .replace(trimmed.as_ref(), "");
    trimmed.trim().to_string()
}

fn is_section_header_text(text: &str) -> bool {
    if text.trim().is_empty() {
        return false;
    }
    let normalized = re_compact_space()
        .replace_all(&text.trim().to_ascii_uppercase(), " ")
        .to_string();
    if matches!(
        normalized.as_str(),
        "MEAT" | "SEAFOOD" | "PRODUCE" | "DELI" | "GROCERY" | "BAKERY" | "FROZEN"
    ) {
        return true;
    }
    if re_section_header_with_aisle().is_match(&normalized) {
        return true;
    }
    if re_section_aisle_prefix().is_match(&normalized) {
        let tokens: HashSet<String> = re_ascii_words()
            .find_iter(&normalized)
            .map(|m| m.as_str().to_string())
            .collect();
        if tokens.iter().any(|token| {
            matches!(
                token.as_str(),
                "MEAT" | "SEAFOOD" | "PRODUCE" | "DELI" | "GROCERY" | "BAKERY" | "FROZEN"
            )
        }) {
            return true;
        }
    }
    false
}

fn looks_like_summary_line(text: &str) -> bool {
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

fn line_has_trailing_price(text: &str) -> bool {
    re_trailing_price().is_match(&normalize_decimal_spacing(text.trim()))
}

fn looks_like_onsale_marker(text: &str) -> bool {
    if text.trim().is_empty() {
        return false;
    }
    let normalized = normalize_decimal_spacing(&text.to_ascii_uppercase());
    let without_price = re_trailing_price().replace(&normalized, "").to_string();
    let compact: String = without_price
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .collect();
    Regex::new(r"(?:[A-Z0-9]{0,3})?ONSAL[E]?$")
        .unwrap()
        .is_match(&compact)
}

fn is_priced_generic_item_label(left_text: &str, full_text: &str) -> bool {
    !left_text.is_empty()
        && line_has_trailing_price(full_text)
        && matches!(
            left_text.trim().to_ascii_uppercase().as_str(),
            "MEAT" | "BAKERY"
        )
}

fn parse_quantity_modifier(line: &str) -> Option<QuantityModifier> {
    let normalized = normalize_decimal_spacing(line.trim());

    if let Some(captures) = re_count_at_price().captures(&normalized) {
        let quantity = captures.get(1)?.as_str().parse::<i32>().ok()?;
        let unit_price_cents = parse_cents(captures.get(2)?.as_str())?;
        return Some(QuantityModifier {
            quantity,
            unit_price_cents: Some(unit_price_cents),
            weight_text: None,
            deal_price_cents: None,
            pattern_type: QuantityPatternType::CountAtPrice,
        });
    }

    if let Some(captures) = re_weight_at_price().captures(&normalized) {
        return Some(QuantityModifier {
            quantity: 1,
            unit_price_cents: None,
            weight_text: Some(captures.get(1)?.as_str().to_string()),
            deal_price_cents: None,
            pattern_type: QuantityPatternType::WeightAtPrice,
        });
    }

    if let Some(captures) = re_multi_for_price().captures(&normalized) {
        let quantity = captures.get(1)?.as_str().parse::<i32>().ok()?;
        let deal_price_cents = parse_cents(captures.get(2)?.as_str())?;
        return Some(QuantityModifier {
            quantity,
            unit_price_cents: Some(deal_price_cents / i64::from(quantity)),
            weight_text: None,
            deal_price_cents: Some(deal_price_cents),
            pattern_type: QuantityPatternType::MultiForPrice,
        });
    }

    None
}

fn validate_quantity_price(total_price_cents: i64, modifier: &QuantityModifier) -> bool {
    let tolerance = 2i64;
    match modifier.pattern_type {
        QuantityPatternType::CountAtPrice => modifier
            .unit_price_cents
            .map(|unit| {
                (unit * i64::from(modifier.quantity) - total_price_cents).abs() <= tolerance
            })
            .unwrap_or(false),
        QuantityPatternType::MultiForPrice => modifier
            .deal_price_cents
            .map(|deal| (deal - total_price_cents).abs() <= tolerance)
            .unwrap_or(false),
        QuantityPatternType::WeightAtPrice => true,
    }
}

fn looks_like_quantity_expression(text: &str) -> bool {
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

    re_negative_unit_qty().is_match(&normalized)
        || Regex::new(r"(?i)^\d+\s*/\s*for\b")
            .unwrap()
            .is_match(&normalized)
        || re_compact_offer_fragment().is_match(&normalized)
        || re_multi_for_price().is_match(&normalized)
        || re_parenthetical_offer_prefix().is_match(&normalized)
}

fn extract_trailing_price_cents(line: &str) -> Option<(i64, bool, usize)> {
    let captures = re_trailing_price().captures(line)?;
    let cents = parse_cents(captures.get(1)?.as_str())?;
    let is_discount = captures.get(2).map(|m| m.as_str() == "-").unwrap_or(false);
    let start = captures.get(1)?.start();
    Some((if is_discount { -cents } else { cents }, is_discount, start))
}

fn is_descriptive_candidate(text: &str) -> bool {
    if text.is_empty() || text.len() <= 2 {
        return false;
    }
    if re_skip_patterns().is_match(text) {
        return false;
    }
    if looks_like_summary_line(text) {
        return false;
    }
    if looks_like_quantity_expression(text) {
        return false;
    }
    if re_trailing_price().is_match(text) {
        return false;
    }
    if re_standalone_price_line().is_match(text) {
        return false;
    }
    if re_long_digits_line().is_match(text) {
        return false;
    }
    let cleaned = strip_leading_receipt_codes(text);
    if cleaned.is_empty() {
        return false;
    }
    if looks_like_onsale_marker(&cleaned) {
        return false;
    }
    if is_section_header_text(&cleaned) {
        return false;
    }
    alpha_ratio(&cleaned) >= 0.4
}

fn merge_description_context(lines: &[String], base: &str, source_idx: usize) -> String {
    let mut merged = base.trim().to_string();
    if source_idx > 0 {
        let prev_line = lines[source_idx - 1].trim();
        let prev_clean = strip_leading_receipt_codes(prev_line);
        if !prev_clean.is_empty()
            && prev_clean.ends_with('-')
            && is_descriptive_candidate(prev_line)
        {
            merged = format!("{prev_clean} {merged}").trim().to_string();
        }
    }
    if source_idx + 1 < lines.len() {
        let next_line = lines[source_idx + 1].trim();
        let next_clean = strip_leading_receipt_codes(next_line);
        if !next_clean.is_empty() && merged.ends_with('-') && is_descriptive_candidate(next_line) {
            merged = format!("{merged} {next_clean}").trim().to_string();
        }
    }
    re_compact_space().replace_all(&merged, " ").to_string()
}

fn is_weak_inline_description(text: &str) -> bool {
    let stripped = text.trim();
    if stripped.is_empty() {
        return false;
    }
    re_weak_parenthetical().is_match(stripped) || re_weak_measure().is_match(stripped)
}

fn maybe_push_warning(warnings: &mut Vec<TextParserWarning>, items_len: usize, message: String) {
    warnings.push(TextParserWarning {
        message,
        after_item_index: if items_len > 0 {
            Some(items_len - 1)
        } else {
            None
        },
    });
}

fn truncated_context(line: &str) -> String {
    let mut context = line.trim().to_string();
    if context.len() > 80 {
        context.truncate(80);
    }
    context
}

fn extract_trailing_noisy_price(line: &str) -> Option<(String, String, i64, usize)> {
    let captures = re_trailing_noisy_price().captures(line)?;
    let whole = captures.get(1)?.as_str().to_string();
    let fraction = captures.get(2)?.as_str().to_string();
    let whole_dollars = whole.parse::<i64>().ok()?;
    let start = captures.get(1)?.start();
    Some((
        format!("{whole}.{fraction}"),
        fraction,
        whole_dollars,
        start,
    ))
}

fn build_malformed_price_candidate(line: &str) -> Option<MalformedTrailingPriceCandidate> {
    let line_upper = line.to_ascii_uppercase();
    if line_upper.contains("TOTAL")
        || line_upper.contains("SUBTOTAL")
        || line_upper.contains("SUB TOTAL")
        || re_tax_tokens().is_match(&line_upper)
    {
        return None;
    }

    let (observed_token, observed_fraction, whole_dollars, price_start) =
        extract_trailing_noisy_price(line)?;
    let desc_part = line[..price_start].trim();
    if desc_part.is_empty() {
        return None;
    }

    let cleaned = strip_leading_receipt_codes(desc_part);
    if cleaned.is_empty()
        || cleaned.len() <= 2
        || looks_like_summary_line(&cleaned)
        || looks_like_quantity_expression(&cleaned)
        || is_section_header_text(&cleaned)
        || alpha_ratio(&cleaned) < 0.4
    {
        return None;
    }

    Some(MalformedTrailingPriceCandidate {
        description: cleaned.clone(),
        category_source: cleaned,
        observed_token,
        observed_fraction,
        whole_dollars,
        context: truncated_context(line),
    })
}

fn levenshtein_distance(left: &str, right: &str) -> usize {
    let left_chars: Vec<char> = left.chars().collect();
    let right_chars: Vec<char> = right.chars().collect();
    let mut prev = (0..=right_chars.len()).collect::<Vec<_>>();
    let mut curr = vec![0; right_chars.len() + 1];

    for (i, left_char) in left_chars.iter().enumerate() {
        curr[0] = i + 1;
        for (j, right_char) in right_chars.iter().enumerate() {
            let substitution_cost = usize::from(left_char != right_char);
            curr[j + 1] = (prev[j + 1] + 1)
                .min(curr[j] + 1)
                .min(prev[j] + substitution_cost);
        }
        std::mem::swap(&mut prev, &mut curr);
    }

    prev[right_chars.len()]
}

fn malformed_candidate_price_options(
    candidate: &MalformedTrailingPriceCandidate,
) -> Vec<CandidatePriceOption> {
    let mut best_by_price: HashMap<i64, usize> = HashMap::new();

    for cents in 0..=99i64 {
        let fraction = format!("{cents:02}");
        let score = levenshtein_distance(&candidate.observed_fraction, &fraction);
        if score > 2 {
            continue;
        }
        let price_cents = candidate.whole_dollars * 100 + cents;
        best_by_price
            .entry(price_cents)
            .and_modify(|best_score| *best_score = (*best_score).min(score))
            .or_insert(score);
    }

    let mut options = best_by_price
        .into_iter()
        .map(|(price_cents, score)| CandidatePriceOption { price_cents, score })
        .collect::<Vec<_>>();
    options.sort_by_key(|option| (option.score, option.price_cents));
    options
}

#[derive(Clone, Debug)]
struct ReconciliationState {
    score: usize,
    prices: Vec<i64>,
    ambiguous: bool,
}

#[derive(Clone, Debug)]
struct ReconciledMalformedPrices {
    prices: Vec<i64>,
}

fn reconcile_malformed_price_candidates(
    regular_total_cents: i64,
    summary_amounts: &HashSet<i64>,
    candidates: &[MalformedTrailingPriceCandidate],
) -> Option<ReconciledMalformedPrices> {
    if candidates.is_empty() {
        return None;
    }

    let mut results = Vec::new();
    let mut targets = summary_amounts
        .iter()
        .copied()
        .filter(|amount| *amount >= regular_total_cents)
        .collect::<Vec<_>>();
    targets.sort_unstable();
    targets.dedup();

    for target in targets {
        let mut states = HashMap::new();
        states.insert(
            regular_total_cents,
            ReconciliationState {
                score: 0,
                prices: Vec::new(),
                ambiguous: false,
            },
        );

        let mut failed_target = false;
        for candidate in candidates {
            let options = malformed_candidate_price_options(candidate);
            if options.is_empty() {
                failed_target = true;
                break;
            }

            let mut next_states: HashMap<i64, ReconciliationState> = HashMap::new();
            for (running_total, state) in &states {
                for option in &options {
                    let next_total = running_total + option.price_cents;
                    if next_total > target {
                        continue;
                    }
                    let next_score = state.score + option.score;
                    let mut next_prices = state.prices.clone();
                    next_prices.push(option.price_cents);

                    match next_states.get_mut(&next_total) {
                        Some(existing) => {
                            if next_score < existing.score {
                                *existing = ReconciliationState {
                                    score: next_score,
                                    prices: next_prices,
                                    ambiguous: state.ambiguous,
                                };
                            } else if next_score == existing.score
                                && (existing.prices != next_prices
                                    || existing.ambiguous
                                    || state.ambiguous)
                            {
                                existing.ambiguous = true;
                            }
                        }
                        None => {
                            next_states.insert(
                                next_total,
                                ReconciliationState {
                                    score: next_score,
                                    prices: next_prices,
                                    ambiguous: state.ambiguous,
                                },
                            );
                        }
                    }
                }
            }
            states = next_states;
            if states.is_empty() {
                failed_target = true;
                break;
            }
        }

        if failed_target {
            continue;
        }

        let Some(state) = states.get(&target) else {
            continue;
        };
        if state.ambiguous {
            continue;
        }
        results.push((state.score, state.prices.clone()));
    }

    results.sort_by_key(|(score, prices)| (*score, prices.clone()));
    let (best_score, best_prices) = results.first()?.clone();
    if results
        .iter()
        .skip(1)
        .any(|(score, prices)| *score == best_score && *prices != best_prices)
    {
        return None;
    }

    Some(ReconciledMalformedPrices {
        prices: best_prices,
    })
}

pub(crate) fn extract_text_items(
    lines: &[String],
    summary_amounts: &HashSet<i64>,
) -> (Vec<ParsedTextItem>, Vec<TextParserWarning>) {
    let mut deferred = Vec::new();
    let normalized_lines: Vec<String> = lines
        .iter()
        .map(|line| normalize_decimal_spacing(line))
        .collect();

    let total_line_idx = normalized_lines.iter().position(|line| {
        re_total_word().is_match(line) && !line.to_ascii_uppercase().contains("SUBTOTAL")
    });

    for (i, line) in normalized_lines.iter().enumerate() {
        if total_line_idx.is_some_and(|total_idx| i > total_idx) {
            break;
        }
        if re_skip_patterns().is_match(line) {
            continue;
        }
        if line.len() < 3 || re_digits_only().is_match(line) {
            continue;
        }

        let is_qty_line = looks_like_quantity_expression(line);
        let has_trailing_total = re_trailing_total_presence().is_match(line);
        if is_qty_line && !has_trailing_total {
            if line.to_ascii_lowercase().contains("/for") {
                let tail_token = re_tail_token()
                    .captures(line)
                    .and_then(|captures| captures.get(1).map(|m| m.as_str().to_string()))
                    .unwrap_or_default();
                if !tail_token.is_empty() && tail_token.chars().any(|ch| ch.is_ascii_alphabetic()) {
                    let context = truncated_context(line);
                    deferred.push(DeferredTextOutcome::Warning(
                        format!(
                            "maybe missed item near malformed multi-buy total \"{tail_token}\" (context: \"{context}\")"
                        ),
                    ));
                }
            }
            continue;
        }

        if re_parenthetical_only().is_match(line) && !re_trailing_price().is_match(line) {
            continue;
        }

        if let Some((price_cents, _is_discount, price_start)) = extract_trailing_price_cents(line) {
            let line_upper = line.to_ascii_uppercase();
            let mut desc_part = line[..price_start].trim().to_string();
            let compact_line = re_compact_space().replace_all(&line_upper, "").to_string();
            let mut prefer_forward_desc = false;
            let mut skip_if_no_forward_desc = false;

            let has_reg_marker = line_upper.contains("REG$")
                || line_upper.contains("@REG")
                || line_upper.contains("0REG")
                || line_upper.contains("OREG")
                || re_reg_price_marker().is_match(&line_upper);

            if has_reg_marker {
                let prices: Vec<_> = re_find_prices().find_iter(line).collect();
                if prices.len() == 1 {
                    let mut marker: String = desc_part
                        .to_ascii_uppercase()
                        .chars()
                        .filter(|ch| ch.is_ascii_alphanumeric())
                        .collect();
                    marker = Regex::new(r"^\d+")
                        .unwrap()
                        .replace(&marker, "")
                        .to_string();
                    if marker.ends_with("REG") {
                        continue;
                    }
                }
                if prices.len() > 1
                    && i > 0
                    && re_trailing_price().is_match(&normalized_lines[i - 1])
                {
                    prefer_forward_desc = true;
                    skip_if_no_forward_desc = true;
                }
            }

            // Skip ghost promo artifacts like "EG2.99" where letters and price
            // run together.  Only fire when the *original* (uncompacted) line also
            // matches — lines with clear whitespace separation (e.g. "Meat 20.53")
            // are real items, not ghosts.
            if re_compact_promo_ghost().is_match(&compact_line)
                && re_compact_promo_ghost().is_match(line_upper.trim())
                && !looks_like_onsale_marker(&desc_part)
            {
                if i > 0 && line_has_trailing_price(&normalized_lines[i - 1]) {
                    continue;
                }
            }

            if line_upper.contains("TOTAL")
                || line_upper.contains("SUBTOTAL")
                || line_upper.contains("SUB TOTAL")
            {
                continue;
            }

            if i > 0 && summary_amounts.contains(&price_cents.abs()) {
                let prev_upper = normalized_lines[i - 1].to_ascii_uppercase();
                if prev_upper.contains("TOTAL")
                    || prev_upper.contains("SUBTOTAL")
                    || prev_upper.contains("SUB TOTAL")
                {
                    continue;
                }
            }

            let weak_inline_desc = is_weak_inline_description(&desc_part);
            let mut force_backward =
                line_upper.contains("REG$") || line_upper.contains("@REG") || weak_inline_desc;
            if has_reg_marker
                && force_backward
                && i > 0
                && !normalized_lines[i - 1].trim().is_empty()
                && line_has_trailing_price(normalized_lines[i - 1].trim())
                && desc_part.starts_with('(')
            {
                prefer_forward_desc = true;
            }

            if !desc_part.is_empty() {
                desc_part = Regex::new(r"^\d{8,}\s*")
                    .unwrap()
                    .replace(&desc_part, "")
                    .to_string();
            }
            let is_onsale_marker_desc = looks_like_onsale_marker(&desc_part);
            if is_onsale_marker_desc {
                prefer_forward_desc = true;
                if i > 0 && line_has_trailing_price(normalized_lines[i - 1].trim()) {
                    skip_if_no_forward_desc = true;
                }
            }

            let is_priced_section_header = !desc_part.is_empty()
                && is_section_header_text(&desc_part)
                && !is_priced_generic_item_label(&desc_part, line);
            let mut skip_section_header_price = false;
            if is_priced_section_header {
                desc_part.clear();
                for j in (i + 1)..normalized_lines.len().min(i + 4) {
                    let next_line = normalized_lines[j].trim();
                    if next_line.is_empty() {
                        continue;
                    }
                    if looks_like_summary_line(next_line) {
                        break;
                    }
                    if let Some((next_price, _, _)) = extract_trailing_price_cents(next_line) {
                        if next_price == price_cents {
                            skip_section_header_price = true;
                        }
                    }
                    break;
                }
            }
            if skip_section_header_price {
                continue;
            }

            let is_malformed_price_marker = !desc_part.is_empty()
                && desc_part.starts_with('(')
                && desc_part.contains('$')
                && !desc_part.contains(' ')
                && desc_part.len() <= 16
                && !desc_part.contains('@')
                && !desc_part.to_ascii_uppercase().contains("REG");
            let is_quantity_stub = re_malformed_price_marker().is_match(&desc_part);
            let mut is_qty_expr = if !desc_part.is_empty() {
                looks_like_quantity_expression(&desc_part)
                    || re_onsale_parenthetical().is_match(&desc_part)
                    || is_onsale_marker_desc
            } else {
                false
            };

            if is_malformed_price_marker {
                let prev_line = if i > 0 {
                    normalized_lines[i - 1].trim()
                } else {
                    ""
                };
                let next_line = if i + 1 < normalized_lines.len() {
                    normalized_lines[i + 1].trim()
                } else {
                    ""
                };
                let prev_looks_like_description = !prev_line.is_empty()
                    && !re_skip_patterns().is_match(prev_line)
                    && !looks_like_summary_line(prev_line)
                    && !looks_like_quantity_expression(prev_line)
                    && !line_has_trailing_price(prev_line);
                let next_supports_multi_buy =
                    !next_line.is_empty() && looks_like_quantity_expression(next_line);
                if prev_looks_like_description && next_supports_multi_buy {
                    force_backward = true;
                    desc_part.clear();
                    is_qty_expr = false;
                } else {
                    continue;
                }
            }
            if is_quantity_stub {
                continue;
            }

            if !desc_part.is_empty() && desc_part.len() > 2 && !is_qty_expr && !force_backward {
                deferred.push(DeferredTextOutcome::Item(ParsedTextItem {
                    description: desc_part.clone(),
                    category_source: desc_part,
                    price_cents,
                    quantity: 1,
                }));
            } else {
                let mut qty_info = Vec::new();
                let mut qty_modifiers = Vec::new();
                let mut found_desc: Option<String> = None;
                let mut found_desc_line_idx: Option<usize> = None;

                if is_priced_section_header {
                    for j in (i + 1)..normalized_lines.len().min(i + 5) {
                        let next_line = normalized_lines[j].trim();
                        if next_line.is_empty()
                            || re_skip_patterns().is_match(next_line)
                            || looks_like_summary_line(next_line)
                            || looks_like_quantity_expression(next_line)
                            || looks_like_onsale_marker(next_line)
                            || re_trailing_price().is_match(next_line)
                            || re_standalone_price_line().is_match(next_line)
                            || re_long_digits_line().is_match(next_line)
                        {
                            continue;
                        }
                        let cleaned_next = strip_leading_receipt_codes(next_line);
                        if cleaned_next.is_empty() || is_section_header_text(&cleaned_next) {
                            continue;
                        }
                        if alpha_ratio(&cleaned_next) < 0.5 {
                            continue;
                        }
                        found_desc = Some(cleaned_next);
                        found_desc_line_idx = Some(j);
                        break;
                    }
                }
                if is_priced_section_header && found_desc.is_none() {
                    continue;
                }

                if found_desc.is_none() && prefer_forward_desc {
                    for j in (i + 1)..normalized_lines.len().min(i + 5) {
                        let next_line = normalized_lines[j].trim();
                        if next_line.is_empty()
                            || re_skip_patterns().is_match(next_line)
                            || looks_like_summary_line(next_line)
                            || looks_like_quantity_expression(next_line)
                            || looks_like_onsale_marker(next_line)
                            || line_has_trailing_price(next_line)
                        {
                            continue;
                        }
                        let cleaned_next = strip_leading_receipt_codes(next_line);
                        if cleaned_next.is_empty() || is_section_header_text(&cleaned_next) {
                            continue;
                        }
                        if alpha_ratio(&cleaned_next) < 0.5 {
                            continue;
                        }
                        found_desc = Some(cleaned_next);
                        found_desc_line_idx = Some(j);
                        break;
                    }
                }
                if skip_if_no_forward_desc && found_desc.is_none() {
                    continue;
                }

                if found_desc.is_none() {
                    let lower_bound = i.saturating_sub(5);
                    for j in (lower_bound..i).rev() {
                        let prev_line = normalized_lines[j].trim();
                        if Regex::new(r"^[\d.]+\s*[HhTtJj]?\s*$")
                            .unwrap()
                            .is_match(prev_line)
                            || Regex::new(r"^\d{8,}$").unwrap().is_match(prev_line)
                            || re_skip_patterns().is_match(prev_line)
                        {
                            continue;
                        }
                        if let Some(modifier) = parse_quantity_modifier(prev_line) {
                            qty_modifiers.push(modifier);
                            qty_info.push(prev_line.to_string());
                            continue;
                        }
                        if looks_like_quantity_expression(prev_line) {
                            qty_info.push(prev_line.to_string());
                            continue;
                        }
                        if looks_like_onsale_marker(prev_line)
                            || re_price_info_line().is_match(prev_line)
                            || re_parenthetical_closed().is_match(prev_line)
                            || (prev_line.starts_with('(') && !prev_line.contains(')'))
                            || re_onsale_parenthetical().is_match(prev_line)
                            || re_parenthetical_multibuy().is_match(prev_line)
                            || prev_line.len() <= 3
                        {
                            continue;
                        }

                        let desc_for_ratio = strip_leading_receipt_codes(prev_line);
                        if alpha_ratio(&desc_for_ratio) < 0.5 {
                            continue;
                        }
                        if prev_line.len() > 2
                            && !Regex::new(r"^[\d.]+$").unwrap().is_match(prev_line)
                        {
                            let cleaned_prev = strip_leading_receipt_codes(prev_line);
                            if !cleaned_prev.is_empty() {
                                found_desc = Some(cleaned_prev);
                                found_desc_line_idx = Some(j);
                                break;
                            }
                        }
                    }
                }

                if let Some(mut found_desc_value) = found_desc {
                    if let Some(source_idx) = found_desc_line_idx {
                        found_desc_value = merge_description_context(
                            &normalized_lines,
                            &found_desc_value,
                            source_idx,
                        );
                    }
                    if weak_inline_desc {
                        found_desc_value =
                            format!("{found_desc_value} {desc_part}").trim().to_string();
                    }
                    let mut quantity = 1;
                    let mut description_suffix = String::new();

                    if let Some(modifier) = qty_modifiers.first() {
                        if validate_quantity_price(price_cents, modifier) {
                            quantity = modifier.quantity;
                            if let Some(weight_text) = &modifier.weight_text {
                                description_suffix = format!(" ({weight_text} lb)");
                            }
                        } else if !qty_info.is_empty() {
                            let reversed: Vec<String> = qty_info.iter().rev().cloned().collect();
                            description_suffix = format!(" ({})", reversed.join(", "));
                        }
                    } else if !qty_info.is_empty() {
                        let reversed: Vec<String> = qty_info.iter().rev().cloned().collect();
                        description_suffix = format!(" ({})", reversed.join(", "));
                    }

                    deferred.push(DeferredTextOutcome::Item(ParsedTextItem {
                        category_source: found_desc_value.clone(),
                        description: format!("{found_desc_value}{description_suffix}"),
                        price_cents,
                        quantity,
                    }));
                } else if price_cents > 0 {
                    let mut message =
                        format!("maybe missed item near price {}", format_cents(price_cents));
                    let context = truncated_context(line);
                    if !context.is_empty() {
                        message.push_str(&format!(" (context: \"{context}\")"));
                    }
                    deferred.push(DeferredTextOutcome::Warning(message));
                }
            }
        } else if let Some(candidate) = build_malformed_price_candidate(line) {
            deferred.push(DeferredTextOutcome::Malformed(candidate));
        } else if let Some(captures) = re_malformed_ocr_price().captures(line) {
            let token = captures.get(1).map(|m| m.as_str()).unwrap_or("");
            let context = truncated_context(line);
            deferred.push(DeferredTextOutcome::Warning(format!(
                "maybe missed item with malformed OCR price \"{token}\" (context: \"{context}\")"
            )));
        } else if line.to_ascii_lowercase().contains("/for")
            && re_tail_token().is_match(line)
            && re_tail_token()
                .captures(line)
                .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
                .is_some_and(|tail| tail.chars().any(|ch| ch.is_ascii_alphabetic()))
        {
            let tail_token = re_tail_token()
                .captures(line)
                .and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
                .unwrap_or_default();
            let context = truncated_context(line);
            deferred.push(DeferredTextOutcome::Warning(
                format!(
                    "maybe missed item near malformed multi-buy total \"{tail_token}\" (context: \"{context}\")"
                ),
            ));
        }
    }

    let regular_total_cents = deferred
        .iter()
        .filter_map(|outcome| match outcome {
            DeferredTextOutcome::Item(item) => Some(item.price_cents),
            _ => None,
        })
        .sum();
    let malformed_candidates = deferred
        .iter()
        .filter_map(|outcome| match outcome {
            DeferredTextOutcome::Malformed(candidate) => Some(candidate.clone()),
            _ => None,
        })
        .collect::<Vec<_>>();
    let reconciled = reconcile_malformed_price_candidates(
        regular_total_cents,
        summary_amounts,
        &malformed_candidates,
    )
    .map(|resolved| resolved.prices.into_iter());

    let mut malformed_prices = reconciled;
    let mut items = Vec::new();
    let mut warnings = Vec::new();
    for outcome in deferred {
        match outcome {
            DeferredTextOutcome::Item(item) => items.push(item),
            DeferredTextOutcome::Warning(message) => {
                maybe_push_warning(&mut warnings, items.len(), message);
            }
            DeferredTextOutcome::Malformed(candidate) => {
                if let Some(recovered_price_cents) =
                    malformed_prices.as_mut().and_then(|prices| prices.next())
                {
                    items.push(ParsedTextItem {
                        description: candidate.description.clone(),
                        category_source: candidate.category_source.clone(),
                        price_cents: recovered_price_cents,
                        quantity: 1,
                    });
                    maybe_push_warning(
                        &mut warnings,
                        items.len(),
                        format!(
                            "auto-corrected malformed OCR price \"{}\" -> \"{}\" using summary reconciliation",
                            candidate.observed_token,
                            format_cents(recovered_price_cents),
                        ),
                    );
                } else {
                    maybe_push_warning(
                        &mut warnings,
                        items.len(),
                        format!(
                            "maybe missed item with malformed OCR price \"{}\" (context: \"{}\")",
                            candidate.observed_token, candidate.context
                        ),
                    );
                }
            }
        }
    }

    (items, warnings)
}

#[cfg(test)]
mod tests {
    use super::extract_text_items;
    use std::collections::HashSet;

    #[test]
    fn recovers_unique_malformed_three_decimal_prices_via_summary_reconciliation() {
        let lines = vec![
            "COSTCO".to_string(),
            "435259 2% FINE-FILT 6.691".to_string(),
            "430 XL EGGS 9.651".to_string(),
            "SUBTOTAL 16.38".to_string(),
            "TOTAL 16.38".to_string(),
        ];
        let summary_amounts = HashSet::from([1638]);

        let (items, warnings) = extract_text_items(&lines, &summary_amounts);

        assert_eq!(items.len(), 2);
        assert_eq!(items[0].description, "2% FINE-FILT");
        assert_eq!(items[0].price_cents, 669);
        assert_eq!(items[1].description, "430 XL EGGS");
        assert_eq!(items[1].price_cents, 969);
        assert_eq!(warnings.len(), 2);
        assert!(warnings[0]
            .message
            .contains("auto-corrected malformed OCR price \"6.691\" -> \"6.69\""));
        assert!(warnings[1]
            .message
            .contains("auto-corrected malformed OCR price \"9.651\" -> \"9.69\""));
    }

    #[test]
    fn skips_reg_marker_lines_with_ocr_noise_prefix() {
        let lines = vec![
            "BESTCO FRESH".to_string(),
            "*Frosh Sunkist Orange".to_string(),
            "(W)@REG$1.69".to_string(),
            "2.96 1b @ $0.99/16 2.93".to_string(),
            "TOTAL 2.93".to_string(),
        ];
        let summary_amounts = HashSet::from([293]);

        let (items, _warnings) = extract_text_items(&lines, &summary_amounts);

        // Should produce only one item at $2.93, not a ghost at $1.69
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].price_cents, 293);
        assert!(items[0].description.contains("Sunkist Orange"));
    }

    #[test]
    fn skips_reg_marker_lines_with_garbled_ocr_prefix() {
        let lines = vec![
            "BESTCO FRESH".to_string(),
            "4KSf Big Instant Noodles ( 6.99".to_string(),
            "(K4AM0REG$7.99".to_string(),
            "Fish Spape Cracker (Tomat 1.99".to_string(),
            "TOTAL 8.98".to_string(),
        ];
        let summary_amounts = HashSet::from([898]);

        let (items, _warnings) = extract_text_items(&lines, &summary_amounts);

        // Should NOT create a ghost item at $7.99 from the REG marker line
        let prices: Vec<i64> = items.iter().map(|i| i.price_cents).collect();
        assert!(
            !prices.contains(&799),
            "REG marker line should not produce a ghost item at $7.99, got items: {:?}",
            items.iter().map(|i| (&i.description, i.price_cents)).collect::<Vec<_>>()
        );
    }

    #[test]
    fn leaves_malformed_three_decimal_prices_as_warnings_without_corroborating_summary_amount() {
        let lines = vec![
            "TEST SHOP".to_string(),
            "MILK 2.991".to_string(),
            "TOTAL 2.99".to_string(),
        ];
        let summary_amounts = HashSet::new();

        let (items, warnings) = extract_text_items(&lines, &summary_amounts);

        assert!(items.is_empty());
        assert_eq!(warnings.len(), 1);
        assert!(warnings[0]
            .message
            .contains("maybe missed item with malformed OCR price \"2.991\""));
    }
}
