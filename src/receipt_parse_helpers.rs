use regex::Regex;
use std::cmp::Reverse;
use std::sync::OnceLock;

#[derive(Clone, Debug)]
pub(crate) struct MerchantWordInput {
    pub(crate) confidence: f64,
    pub(crate) has_bbox: bool,
}

#[derive(Clone, Debug)]
pub(crate) struct MerchantLineInput {
    pub(crate) text: String,
    pub(crate) words: Vec<MerchantWordInput>,
}

#[derive(Clone, Debug)]
pub(crate) struct MerchantPageInput {
    pub(crate) lines: Vec<MerchantLineInput>,
}

fn re_numeric_date_like() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[\d/\-:]+$").unwrap())
}

fn re_clean_merchant() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[^\w\s&'-]").unwrap())
}

fn re_spatial_w_price() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"W\s+\$\d+\.\d{2}").unwrap())
}

const MIN_LINE_CONFIDENCE: f64 = 0.6;

fn clean_merchant_candidate(value: &str) -> String {
    re_clean_merchant()
        .replace_all(value, "")
        .trim()
        .to_string()
}

pub(crate) fn extract_merchant_with_confidence(pages: &[MerchantPageInput]) -> Option<String> {
    if pages.is_empty() {
        return None;
    }

    let mut lines_checked = 0usize;
    for page in pages {
        for line in &page.lines {
            if lines_checked >= 10 {
                return None;
            }
            if line.words.is_empty() {
                continue;
            }
            let avg_confidence = line.words.iter().map(|word| word.confidence).sum::<f64>()
                / line.words.len() as f64;
            if avg_confidence < MIN_LINE_CONFIDENCE {
                lines_checked += 1;
                continue;
            }

            let line_text = line.text.trim();
            if line_text.len() <= 3 || re_numeric_date_like().is_match(line_text) {
                lines_checked += 1;
                continue;
            }

            let cleaned = clean_merchant_candidate(line_text);
            if cleaned.len() > 2 {
                return Some(cleaned);
            }

            lines_checked += 1;
        }
    }

    None
}

pub(crate) fn extract_merchant(
    lines: &[String],
    full_text: &str,
    pages: &[MerchantPageInput],
    known_merchants: &[String],
) -> String {
    let full_text_upper = full_text.to_ascii_uppercase();
    let mut merchant_candidates: Vec<String> = known_merchants.to_vec();
    merchant_candidates.sort_by_key(|merchant| Reverse(merchant.len()));
    for merchant in merchant_candidates {
        let pattern = format!(r"\b{}\b", regex::escape(&merchant.to_ascii_uppercase()));
        if Regex::new(&pattern)
            .ok()
            .is_some_and(|regex| regex.is_match(&full_text_upper))
        {
            return merchant;
        }
    }

    // Costco's name is frequently OCR'd with the leading "C" dropped or an
    // O/0 confusable ("OSTCO", "C0STCO"), so the exact \bCOSTCO\b match above
    // misses. The "WHOLESALE" banner is unmistakably Costco, so when it
    // co-occurs with such a token, canonicalize to COSTCO. This branch only
    // runs after the exact known-merchant match has already failed, so a real
    // "COSTCO" receipt never reaches here and ordinary merchants (no
    // "WHOLESALE") are never rewritten.
    if full_text_upper.contains("WHOLESALE")
        && (full_text_upper.contains("OSTCO") || full_text_upper.contains("C0STCO"))
    {
        return "COSTCO".to_string();
    }

    if let Some(confident) = extract_merchant_with_confidence(pages) {
        return confident;
    }

    for line in lines.iter().take(5) {
        if line.len() > 3 && !re_numeric_date_like().is_match(line) {
            let cleaned = clean_merchant_candidate(line);
            if cleaned.len() > 2 {
                return cleaned;
            }
        }
    }

    "UNKNOWN_MERCHANT".to_string()
}

pub(crate) fn has_useful_bbox_data(pages: &[MerchantPageInput]) -> bool {
    if pages.is_empty() {
        return false;
    }
    for line in pages[0].lines.iter().take(10) {
        for word in &line.words {
            if word.has_bbox {
                return true;
            }
        }
    }
    false
}

pub(crate) fn is_spatial_layout_receipt(full_text: &str) -> bool {
    let full_text_upper = full_text.to_ascii_uppercase();
    for merchant in [
        "T&T",
        "T & T",
        "REAL CANADIAN",
        "SUPERSTORE",
        "C&C",
        "C & C",
        "NOFRILLS",
        "NO FRILLS",
        "COSTCO",
        "WHOLESALE",
    ] {
        if full_text_upper.contains(merchant) {
            return true;
        }
    }
    re_spatial_w_price().is_match(full_text)
}

#[cfg(test)]
mod tests {
    use super::extract_merchant;

    #[test]
    fn canonicalizes_costco_ocr_dropped_leading_c() {
        // OCR dropped the leading C; "WHOLESALE" banner confirms Costco.
        let full_text = "OSTCO\nWHOLESALE\nE Markham #545\n1268728 UNREAL 17.99";
        let lines: Vec<String> = full_text.lines().map(str::to_string).collect();
        assert_eq!(
            extract_merchant(&lines, full_text, &[], &["COSTCO".to_string()]),
            "COSTCO"
        );
    }

    #[test]
    fn does_not_rewrite_non_wholesale_merchants() {
        // No "WHOLESALE" banner: a coincidental token must not become COSTCO.
        let full_text = "FRESHCO\nMcCowan & Bur Oak FreshCo";
        let lines: Vec<String> = full_text.lines().map(str::to_string).collect();
        assert_eq!(
            extract_merchant(&lines, full_text, &[], &["COSTCO".to_string()]),
            "FRESHCO"
        );
    }
}
