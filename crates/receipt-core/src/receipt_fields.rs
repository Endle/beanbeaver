use regex::Regex;
use std::cmp::Ordering;
use std::sync::OnceLock;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SimpleDate {
    pub year: i32,
    pub month: u32,
    pub day: u32,
}

#[derive(Clone, Debug)]
struct RankedDateCandidate {
    score: i32,
    line_index: usize,
    start: usize,
    date: SimpleDate,
}

fn re_date_context_hint() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\b(DATE(?:TIME)?|TRANS(?:ACTION)?\s*DATE)\b").unwrap())
}

fn re_separated_date() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(^|[^0-9])(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})([^0-9]|$)").unwrap()
    })
}

fn re_compact_date() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(^|[^0-9])(\d{4})(\d{2})(\d{2})([^0-9]|$)").unwrap())
}

fn re_month_name_date() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{1,2}),?\s+(\d{4})\b",
        )
        .unwrap()
    })
}

// Day-first month-name dates, e.g. "22-May-2026" or "22 May 2026".
fn re_dmy_month_name_date() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)\b(\d{1,2})[-\s]+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*[-\s]+(\d{4})\b",
        )
        .unwrap()
    })
}

fn month_number_from_name(name: &str) -> Option<i32> {
    match name.get(..3).unwrap_or("").to_ascii_lowercase().as_str() {
        "jan" => Some(1),
        "feb" => Some(2),
        "mar" => Some(3),
        "apr" => Some(4),
        "may" => Some(5),
        "jun" => Some(6),
        "jul" => Some(7),
        "aug" => Some(8),
        "sep" => Some(9),
        "oct" => Some(10),
        "nov" => Some(11),
        "dec" => Some(12),
        _ => None,
    }
}

fn re_price_end() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\$?\s*(\d+\.\d{2})\s*$").unwrap())
}

fn re_price_anywhere() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\$?\s*(\d+\.\d{2})").unwrap())
}

fn re_standalone_amount() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\$?\s*\d+\.\d{2}\s*$").unwrap())
}

fn re_tax_tokens() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\b(HST|GST|PST|TAX)\b").unwrap())
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

pub fn extract_price_from_line(line: &str) -> Option<i64> {
    let normalized = normalize_decimal_spacing(line);
    for regex in [re_price_end(), re_price_anywhere()] {
        if let Some(captures) = regex.captures(&normalized) {
            if let Some(token) = captures.get(1) {
                if let Some(value) = parse_cents(token.as_str()) {
                    return Some(value);
                }
            }
        }
    }
    None
}

/// Return the largest price found on the line, or None if no price is present.
/// Used to disambiguate cases like a single OCR line collapsing two columns
/// `TOTAL ... TOTAL TAX ... $74.55 $1.82` — the trailing price is the tax, but
/// the total is by definition the larger of the two.
fn extract_max_price_from_line(line: &str) -> Option<i64> {
    let normalized = normalize_decimal_spacing(line);
    re_price_anywhere()
        .captures_iter(&normalized)
        .filter_map(|captures| captures.get(1).and_then(|m| parse_cents(m.as_str())))
        .max()
}

/// Public total extractor: the raw label-scan pick, then a guarded
/// reconciliation against the payment block (see `reconcile_total_with_charge`).
pub fn extract_total(lines: &[String]) -> i64 {
    reconcile_total_with_charge(lines, extract_total_raw(lines))
}

/// When the on-device box-position artifact mis-pairs the TOTAL label with a
/// neighbouring amount (the tax row, or nothing → 0), the printed charged amount
/// is more reliable. Prefer an amount corroborated by **two** payment-block
/// lines (a card tender and/or an "AMOUNT:" echo), but only when it **exceeds**
/// the raw candidate — so cash-with-change and split-tender receipts, where the
/// real total legitimately exceeds the card portion, are left untouched. On a
/// correctly-paired receipt the candidate already equals the charged amount, so
/// this never fires, keeping desktop output (cached baseline + parity) unchanged.
fn reconcile_total_with_charge(lines: &[String], candidate: i64) -> i64 {
    let mut payment_amounts: Vec<i64> = Vec::new();
    for (idx, line) in lines.iter().enumerate() {
        let upper = line.to_ascii_uppercase();
        let is_payment = upper.contains("AMOUNT:") || matches!(classify_tender_line(&upper), Some("card"));
        if is_payment {
            if let Some(cents) = tender_amount_for_line(lines, idx) {
                if cents > 0 {
                    payment_amounts.push(cents);
                }
            }
        }
    }
    let mut corroborated: Vec<i64> = payment_amounts
        .iter()
        .copied()
        .filter(|&a| a > candidate && payment_amounts.iter().filter(|&&b| b == a).count() >= 2)
        .collect();
    corroborated.sort_unstable();
    corroborated.dedup();
    match corroborated.as_slice() {
        [only] => *only,
        _ => candidate,
    }
}

fn extract_total_raw(lines: &[String]) -> i64 {
    const EXCLUDED_PHRASES: [&str; 6] = [
        "TOTAL DISCOUNT",
        "TOTAL DISCOUNT(S)",
        "TOTAL SAVINGS",
        "TOTAL SAVED",
        "TOTAL NUMBER OF ITEMS",
        "TOTAL ITEMS",
    ];

    for reversed_index in 0..lines.len() {
        let idx = lines.len() - 1 - reversed_index;
        let line_upper = lines[idx].to_ascii_uppercase();
        if line_upper.contains("TOTAL NUMBER") {
            continue;
        }
        if EXCLUDED_PHRASES
            .iter()
            .any(|phrase| line_upper.contains(phrase))
        {
            continue;
        }
        if line_upper.contains("TOTAL") && !line_upper.contains("SUBTOTAL") {
            let prev_upper = if idx > 0 {
                lines[idx - 1].to_ascii_uppercase()
            } else {
                String::new()
            };
            let next_upper = if idx + 1 < lines.len() {
                lines[idx + 1].to_ascii_uppercase()
            } else {
                String::new()
            };
            if next_upper.contains("DISCOUNT") {
                continue;
            }
            if prev_upper.contains("TOTAL NUMBER OF ITEMS SOLD") {
                continue;
            }
            if let Some(amount) = extract_price_from_line(&lines[idx]) {
                if amount == 0
                    && line_upper.contains("AFTER TAX")
                    && idx + 1 < lines.len()
                    && re_standalone_amount().is_match(&lines[idx + 1])
                {
                    if let Some(next_amount) = extract_price_from_line(&lines[idx + 1]) {
                        return next_amount;
                    }
                }
                // Collapsed two-column TOTAL row: when the same line carries
                // both a TOTAL label and a TAX label (e.g. OCR mashes
                // "TOTAL | TOTAL TAX | $74.55 | $1.82" into one line), the
                // trailing price is the tax. Prefer the largest of the two,
                // which by definition is the total.
                if re_tax_tokens().is_match(&line_upper) {
                    if let Some(max_amount) = extract_max_price_from_line(&lines[idx]) {
                        if max_amount > amount {
                            return max_amount;
                        }
                    }
                }
                return amount;
            }
            if idx + 1 < lines.len() {
                if let Some(amount) = extract_price_from_line(&lines[idx + 1]) {
                    return amount;
                }
            }
            if idx > 0 {
                let prev_line_upper = lines[idx - 1].to_ascii_uppercase();
                if !prev_line_upper.contains("TAX")
                    && !prev_line_upper.contains("HST")
                    && !prev_line_upper.contains("GST")
                {
                    if let Some(amount) = extract_price_from_line(&lines[idx - 1]) {
                        return amount;
                    }
                }
            }
            // Costco-style layout: the TOTAL label sits on its own line
            // ("TOTAL.") and the value lives further down in the payment
            // block as a standalone amount (typically a few lines above
            // an "AMOUNT :" label that OCR linearization reorders).
            // Scan forward for the first standalone decimal, stopping
            // at section boundaries that can't be the total.
            const FORWARD_SCAN_WINDOW: usize = 20;
            let upper_bound = (idx + 1 + FORWARD_SCAN_WINDOW).min(lines.len());
            for scan_idx in (idx + 1)..upper_bound {
                let scan_upper = lines[scan_idx].to_ascii_uppercase();
                if scan_upper.contains("SUBTOTAL")
                    || scan_upper.contains("CHANGE")
                    || scan_upper.contains("BALANCE")
                {
                    break;
                }
                if re_standalone_amount().is_match(&lines[scan_idx]) {
                    if let Some(amount) = extract_price_from_line(&lines[scan_idx]) {
                        return amount;
                    }
                }
            }
        }
    }
    0
}

pub fn extract_tax(lines: &[String]) -> Option<i64> {
    for idx in (0..lines.len()).rev() {
        let line_upper = lines[idx].to_ascii_uppercase();
        if line_upper.contains("SUBTOTAL") || line_upper.contains("SUB TOTAL") {
            continue;
        }
        if line_upper.contains("TAXED") || line_upper.contains("TAXABLE") {
            continue;
        }
        if line_upper.contains("TOTAL") && line_upper.contains("AFTER TAX") {
            continue;
        }

        let has_total = line_upper.contains("TOTAL");
        let has_tax_keyword = re_tax_tokens().is_match(&line_upper);
        if has_total && !has_tax_keyword {
            continue;
        }

        if has_tax_keyword {
            if let Some(amount) = extract_price_from_line(&lines[idx]) {
                return Some(amount);
            }

            if idx + 1 < lines.len() {
                let next_line = &lines[idx + 1];
                let next_line_upper = next_line.to_ascii_uppercase();
                let mut is_total_value = next_line_upper.contains("TOTAL");
                if !is_total_value && idx + 2 < lines.len() {
                    let line_i2_upper = lines[idx + 2].to_ascii_uppercase();
                    if line_i2_upper.contains("TOTAL") && !line_i2_upper.contains("SUBTOTAL") {
                        if idx + 3 < lines.len()
                            && extract_price_from_line(&lines[idx + 3]).is_some()
                        {
                            is_total_value = false;
                        } else {
                            is_total_value = true;
                        }
                    }
                }

                if !is_total_value && re_standalone_amount().is_match(next_line) {
                    if let Some(amount) = extract_price_from_line(next_line) {
                        return Some(amount);
                    }
                }
            }

            if idx > 0 && re_standalone_amount().is_match(&lines[idx - 1]) {
                let prev_upper = lines[idx - 1].to_ascii_uppercase();
                if !prev_upper.contains("SUBTOTAL") && !prev_upper.contains("TOTAL") {
                    if let Some(amount) = extract_price_from_line(&lines[idx - 1]) {
                        return Some(amount);
                    }
                }
            }
        }
    }
    None
}

fn re_subtotal_label() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    // The inner 'O' in SUBTOTAL is the most common OCR victim — accept the
    // usual O-confusables (0/C/Q/D/G). Costco receipts have been observed as
    // SUBTCTAL.
    RE.get_or_init(|| Regex::new(r"SUB\s*T[OCQDG0]TAL").unwrap())
}

#[derive(Clone, Debug)]
pub struct TenderLine {
    pub raw_label: String,
    pub amount_cents: i64,
    pub kind: &'static str,
}

fn classify_tender_line(line_upper: &str) -> Option<&'static str> {
    // Reject noise lines that contain price-paired keywords but aren't tenders.
    if line_upper.contains("REMAINING BALANCE") {
        return None;
    }
    if line_upper.contains("CASH BACK") {
        return None;
    }
    if line_upper.contains("CHANGE") {
        return None;
    }
    if line_upper.contains("AMOUNT:") {
        // Costco prints "AMOUNT: $25.00" as the *card-charge* echo of the next
        // Shop Card line; ignore the echo and let the labelled line classify.
        return None;
    }
    if line_upper.contains("GIFT CARD")
        || line_upper.contains("GIFTCARD")
        || line_upper.contains("GIFT CRD")
        || line_upper.contains("SHOP CARD")
    {
        return Some("gift_card");
    }
    if line_upper.contains("MERCH CRED")
        || line_upper.contains("MERCH CREDIT")
        || line_upper.contains("STORE CREDIT")
    {
        return Some("store_credit");
    }
    if line_upper.contains("MASTERCARD")
        || line_upper.contains("VISA")
        || line_upper.contains("AMEX")
        || line_upper.contains("AMERICAN EXPRESS")
        || line_upper.contains("DEBIT")
    {
        return Some("card");
    }
    if line_upper.contains("CASH") {
        return Some("cash");
    }
    None
}

fn tender_amount_for_line(lines: &[String], idx: usize) -> Option<i64> {
    if let Some(amount) = extract_price_from_line(&lines[idx]) {
        return Some(amount);
    }
    if idx + 1 < lines.len() && re_standalone_amount().is_match(&lines[idx + 1]) {
        return extract_price_from_line(&lines[idx + 1]);
    }
    None
}

fn trim_tender_label(line: &str) -> String {
    let mut text = line.trim().to_string();
    // Strip trailing currency token like "$25.00" so the label reads cleanly.
    if let Some(captures) = re_price_anywhere().captures(&text) {
        if let Some(matched) = captures.get(0) {
            let start = matched.start();
            text = text[..start].trim_end_matches(['$', ' ', ':', '-', '\t']).to_string();
        }
    }
    text
}

/// Scan OCR lines for explicit tender lines (gift card / store credit / cash / card).
///
/// Two-pass behavior: each candidate line picks an amount from the same line or the
/// next standalone-amount line. Reconcile against `total_cents`: if the sum of
/// detected tenders is within $0.05 of the total, return them; otherwise return
/// an empty vec so the caller falls back to the single-payment shape.
pub fn extract_tenders(lines: &[String], total_cents: i64) -> Vec<TenderLine> {
    if total_cents <= 0 || lines.is_empty() {
        return Vec::new();
    }

    let mut tenders: Vec<TenderLine> = Vec::new();
    let mut consumed_next = false;
    for (idx, line) in lines.iter().enumerate() {
        if consumed_next {
            consumed_next = false;
            continue;
        }
        let line_upper = line.to_ascii_uppercase();
        let Some(kind) = classify_tender_line(&line_upper) else {
            continue;
        };
        let Some(amount_cents) = tender_amount_for_line(lines, idx) else {
            continue;
        };
        if amount_cents <= 0 {
            continue;
        }
        // If the amount came from the next standalone-amount line, skip it next iter.
        if extract_price_from_line(line).is_none()
            && idx + 1 < lines.len()
            && re_standalone_amount().is_match(&lines[idx + 1])
        {
            consumed_next = true;
        }
        tenders.push(TenderLine {
            raw_label: trim_tender_label(line),
            amount_cents,
            kind,
        });
    }

    if tenders.is_empty() {
        return tenders;
    }
    let sum: i64 = tenders.iter().map(|t| t.amount_cents).sum();
    if (sum - total_cents).abs() > 5 {
        return Vec::new();
    }
    tenders
}

pub fn extract_subtotal(lines: &[String]) -> Option<i64> {
    for (idx, line) in lines.iter().enumerate() {
        let line_upper = line.to_ascii_uppercase();
        if re_subtotal_label().is_match(&line_upper) {
            if let Some(amount) = extract_price_from_line(line) {
                return Some(amount);
            }
            if idx + 1 < lines.len() {
                if let Some(amount) = extract_price_from_line(&lines[idx + 1]) {
                    return Some(amount);
                }
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::{extract_date, extract_subtotal, extract_tenders, extract_total};

    #[test]
    fn date_parses_day_first_hyphenated_month_name() {
        // Jin Lian Food / Clover format: "22-May-2026 3:22:42p.m."
        let lines = vec!["22-May-2026 3:22:42p.m.".to_string()];
        let parsed = extract_date(&lines, "", 2026).expect("date should parse");
        assert_eq!((parsed.year, parsed.month, parsed.day), (2026, 5, 22));
    }

    #[test]
    fn subtotal_tolerates_costco_subtctal_ocr_typo() {
        // Costco "SUBTOTAL" OCR'd as "SUBTCTAL" (inner O → C).
        let lines = vec![
            "***END OF PRE-SCANNED ITEMS***".to_string(),
            "SUBTCTAL 159.08".to_string(),
            "TAX 14.07".to_string(),
        ];

        assert_eq!(extract_subtotal(&lines), Some(15_908));
    }

    #[test]
    fn total_picks_max_when_total_and_tax_share_a_line() {
        // OCR collapsed Freshco's two-column "TOTAL | TOTAL TAX | $74.55 | $1.82"
        // row into a single line. The trailing price is the tax; the actual
        // total is the larger value.
        let lines = vec![
            "SUBTOTAL $72.73".to_string(),
            "TOTAL TOTAL TAX $74.55 $1.82".to_string(),
        ];

        assert_eq!(extract_total(&lines), 7_455);
    }

    #[test]
    fn total_reconciles_to_corroborated_charge_when_label_mispaired() {
        // On-device box-position artifact: the TOTAL label paired with the tax
        // row (20.14); the real total (245.87) is orphaned but corroborated by
        // the card tender and the AMOUNT: echo. Reconciliation recovers it.
        let lines = vec![
            "TOTAL 20.14".to_string(),
            "245.87".to_string(),
            "AMOUNT: 245.87".to_string(),
            "MasterCard 245.87".to_string(),
        ];
        assert_eq!(extract_total(&lines), 24_587);
    }

    #[test]
    fn total_reconciliation_leaves_correct_total_unchanged() {
        // Correctly paired: the candidate already equals the charged amount, so
        // reconciliation must not fire (this is the desktop/cached-parity guard).
        let lines = vec![
            "TOTAL 50.00".to_string(),
            "AMOUNT: 50.00".to_string(),
            "VISA 50.00".to_string(),
        ];
        assert_eq!(extract_total(&lines), 5_000);
    }

    #[test]
    fn total_reconciliation_ignores_split_tender_card_portion() {
        // Split tender: the real total (50.00) exceeds the card portion (30.00),
        // so the corroborated card+AMOUNT amount must NOT override it.
        let lines = vec![
            "TOTAL 50.00".to_string(),
            "GIFT CARD 20.00".to_string(),
            "AMOUNT: 30.00".to_string(),
            "VISA 30.00".to_string(),
        ];
        assert_eq!(extract_total(&lines), 5_000);
    }

    #[test]
    fn total_reconciliation_holds_on_real_costco_split_tender() {
        // Real Costco split tender (2026-03-07, $466.68 = $25.00 Shop Card +
        // $441.68 MasterCard). The receipt carries two "AMOUNT:" echoes plus the
        // card line, but neither charged amount exceeds the printed total, so the
        // `> candidate` guard must leave 466.68 intact. Exercises the two-AMOUNT,
        // gift-card-classified shape the synthetic split-tender case above misses.
        let lines = vec![
            "TOTAL 466.68".to_string(),
            "Shop Card 25.00".to_string(),
            "AMOUNT: $25.00".to_string(),
            "MASTERCARD".to_string(),
            "AMOUNT: 441.68".to_string(),
            "MasterCard 441.68".to_string(),
            "CHANGE 0.00".to_string(),
        ];
        assert_eq!(extract_total(&lines), 46_668);
    }

    #[test]
    fn total_reconciliation_holds_on_real_costco_single_tender() {
        // Real Costco desktop OCR (2026-03-05): TOTAL is already correctly paired
        // and the AMOUNT:/MasterCard echoes equal it, so reconciliation never
        // fires (charge == candidate, not >). Desktop/cached-parity guard.
        let lines = vec![
            "SUBTOTAL 225.73".to_string(),
            "TAX 20.14".to_string(),
            "TOTAL 245.87".to_string(),
            "AMOUNT: 245.87".to_string(),
            "MasterCard 245.87".to_string(),
            "CHANGE 0.00".to_string(),
        ];
        assert_eq!(extract_total(&lines), 24_587);
    }

    #[test]
    fn total_after_tax_zero_prefers_following_standalone_amount() {
        let lines = vec![
            "Item Count: 33".to_string(),
            "Sub Total 153.55".to_string(),
            "HST".to_string(),
            "hst5% 0.00".to_string(),
            "Total after Tax 0.00".to_string(),
            "153.55".to_string(),
            "Credit Card".to_string(),
            "153.55".to_string(),
        ];

        assert_eq!(extract_total(&lines), 15_355);
    }

    #[test]
    fn tenders_split_costco_shop_card_and_mastercard() {
        // Costco prints: AMOUNT: $25.00 / REMAINING BALANCE: $0.00 / Shop Card 25.00
        // / XXXXXXXXXXXX4385 / ACCT: MASTERCARD / (next line) 441.68.
        let lines = vec![
            "TOTAL".to_string(),
            "466.68".to_string(),
            "AMOUNT: $25.00".to_string(),
            "REMAINING BALANCE: $0.00".to_string(),
            "Shop Card".to_string(),
            "25.00".to_string(),
            "XXXXXXXXXXXX4385".to_string(),
            "MASTERCARD".to_string(),
            "441.68".to_string(),
        ];

        let tenders = extract_tenders(&lines, 46_668);
        assert_eq!(tenders.len(), 2);
        assert_eq!(tenders[0].kind, "gift_card");
        assert_eq!(tenders[0].amount_cents, 2_500);
        assert_eq!(tenders[0].raw_label, "Shop Card");
        assert_eq!(tenders[1].kind, "card");
        assert_eq!(tenders[1].amount_cents, 44_168);
        assert_eq!(tenders[1].raw_label, "MASTERCARD");
    }

    #[test]
    fn tenders_returns_empty_when_sum_does_not_reconcile() {
        let lines = vec![
            "TOTAL 50.00".to_string(),
            "MASTERCARD 30.00".to_string(),
        ];
        // Only 30 of 50 covered → reconciliation fails, drop tenders.
        assert!(extract_tenders(&lines, 5_000).is_empty());
    }

    #[test]
    fn tenders_ignores_change_and_cash_back_lines() {
        let lines = vec![
            "TOTAL 20.00".to_string(),
            "CASH 25.00".to_string(),
            "CASH BACK 0.00".to_string(),
            "CHANGE 5.00".to_string(),
        ];
        let tenders = extract_tenders(&lines, 2_000);
        // 25 vs total 20 → 5 off, reconciliation fails → empty.
        assert!(tenders.is_empty());
    }
}

fn to_four_digit_year(year: i32) -> i32 {
    if year < 100 {
        if year <= 69 {
            2000 + year
        } else {
            1900 + year
        }
    } else {
        year
    }
}

fn is_leap_year(year: i32) -> bool {
    (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0)
}

fn safe_date(year: i32, month: i32, day: i32) -> Option<SimpleDate> {
    if !(1..=12).contains(&month) || day < 1 {
        return None;
    }
    let max_day = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if is_leap_year(year) => 29,
        2 => 28,
        _ => return None,
    };
    if day > max_day {
        return None;
    }
    Some(SimpleDate {
        year,
        month: month as u32,
        day: day as u32,
    })
}

fn numeric_date_candidates(
    part1: &str,
    part2: &str,
    part3: &str,
) -> Vec<(SimpleDate, &'static str)> {
    let a = match part1.parse::<i32>() {
        Ok(value) => value,
        Err(_) => return Vec::new(),
    };
    let b = match part2.parse::<i32>() {
        Ok(value) => value,
        Err(_) => return Vec::new(),
    };
    let c = match part3.parse::<i32>() {
        Ok(value) => value,
        Err(_) => return Vec::new(),
    };

    let mut candidates = Vec::new();
    let mut add = |year: i32, month: i32, day: i32, kind: &'static str| {
        if let Some(parsed) = safe_date(year, month, day) {
            candidates.push((parsed, kind));
        }
    };

    if part1.len() == 4 {
        add(a, b, c, "ymd4");
        return candidates;
    }

    if part3.len() == 4 {
        if a > 12 && b <= 12 {
            add(c, b, a, "dmy4");
        } else if b > 12 && a <= 12 {
            add(c, a, b, "mdy4");
        } else {
            add(c, a, b, "mdy4");
            add(c, b, a, "dmy4");
        }
        return candidates;
    }

    let year_a = to_four_digit_year(a);
    let year_c = to_four_digit_year(c);

    if b <= 12 && c <= 31 {
        add(year_a, b, c, "ymd2");
    }
    if a <= 12 && b <= 31 {
        add(year_c, a, b, "mdy2");
    }
    if b <= 12 && a <= 31 {
        add(year_c, b, a, "dmy2");
    }

    candidates
}

fn year_score(candidate_year: i32, current_year: i32) -> i32 {
    10 - (candidate_year - current_year).abs().min(10)
}

fn kind_base_score(kind: &str) -> i32 {
    match kind {
        "ymd4" => 35,
        "ymd2" => 28,
        "mdy4" => 25,
        "dmy4" => 24,
        "mdy2" => 22,
        "dmy2" => 20,
        _ => 0,
    }
}

fn compare_ranked_candidates(left: &RankedDateCandidate, right: &RankedDateCandidate) -> Ordering {
    right
        .score
        .cmp(&left.score)
        .then_with(|| left.line_index.cmp(&right.line_index))
        .then_with(|| left.start.cmp(&right.start))
}

pub fn extract_date(
    lines: &[String],
    full_text: &str,
    current_year: i32,
) -> Option<SimpleDate> {
    if lines.is_empty() && full_text.is_empty() {
        return None;
    }

    let source_lines: Vec<String> = if lines.is_empty() {
        full_text
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .map(str::to_string)
            .collect()
    } else {
        lines.to_vec()
    };
    let current_yy = current_year.rem_euclid(100);
    let mut ranked_candidates = Vec::new();

    for (line_index, line) in source_lines.iter().enumerate() {
        let normalized_line = normalize_decimal_spacing(line);
        let hint_bonus = if re_date_context_hint().is_match(&normalized_line) {
            40
        } else {
            0
        };
        let prefer_year_first = hint_bonus > 0;

        for captures in re_separated_date().captures_iter(&normalized_line) {
            let part1 = captures.get(2).map(|m| m.as_str()).unwrap_or("");
            let part2 = captures.get(3).map(|m| m.as_str()).unwrap_or("");
            let part3 = captures.get(4).map(|m| m.as_str()).unwrap_or("");
            let start = captures.get(2).map(|m| m.start()).unwrap_or(0);
            for (candidate_date, kind) in numeric_date_candidates(part1, part2, part3) {
                if kind == "ymd2" {
                    let year_token = match part1.parse::<i32>() {
                        Ok(value) => value,
                        Err(_) => continue,
                    };
                    if !(prefer_year_first && (20..=current_yy + 1).contains(&year_token)) {
                        continue;
                    }
                }
                let mut base = kind_base_score(kind);
                if kind == "mdy2" {
                    base += 2;
                }
                if kind == "ymd2" && prefer_year_first {
                    base += 3;
                }
                ranked_candidates.push(RankedDateCandidate {
                    score: base + hint_bonus + year_score(candidate_date.year, current_year),
                    line_index,
                    start,
                    date: candidate_date,
                });
            }
        }

        for captures in re_compact_date().captures_iter(&normalized_line) {
            let year = captures.get(2).and_then(|m| m.as_str().parse::<i32>().ok());
            let month = captures.get(3).and_then(|m| m.as_str().parse::<i32>().ok());
            let day = captures.get(4).and_then(|m| m.as_str().parse::<i32>().ok());
            let start = captures.get(2).map(|m| m.start()).unwrap_or(0);
            if let (Some(year), Some(month), Some(day)) = (year, month, day) {
                if let Some(compact_date) = safe_date(year, month, day) {
                    ranked_candidates.push(RankedDateCandidate {
                        score: 30 + hint_bonus + year_score(compact_date.year, current_year),
                        line_index,
                        start,
                        date: compact_date,
                    });
                }
            }
        }

        for captures in re_month_name_date().captures_iter(&normalized_line) {
            let month = captures.get(1).and_then(|m| month_number_from_name(m.as_str()));
            let day = captures.get(2).and_then(|m| m.as_str().parse::<i32>().ok());
            let year = captures.get(3).and_then(|m| m.as_str().parse::<i32>().ok());
            let start = captures.get(1).map(|m| m.start()).unwrap_or(0);
            if let (Some(month), Some(day), Some(year)) = (month, day, year) {
                if let Some(parsed) = safe_date(year, month, day) {
                    ranked_candidates.push(RankedDateCandidate {
                        score: 26 + hint_bonus + year_score(parsed.year, current_year),
                        line_index,
                        start,
                        date: parsed,
                    });
                }
            }
        }

        for captures in re_dmy_month_name_date().captures_iter(&normalized_line) {
            let day = captures.get(1).and_then(|m| m.as_str().parse::<i32>().ok());
            let month = captures.get(2).and_then(|m| month_number_from_name(m.as_str()));
            let year = captures.get(3).and_then(|m| m.as_str().parse::<i32>().ok());
            let start = captures.get(1).map(|m| m.start()).unwrap_or(0);
            if let (Some(month), Some(day), Some(year)) = (month, day, year) {
                if let Some(parsed) = safe_date(year, month, day) {
                    ranked_candidates.push(RankedDateCandidate {
                        score: 26 + hint_bonus + year_score(parsed.year, current_year),
                        line_index,
                        start,
                        date: parsed,
                    });
                }
            }
        }
    }

    if ranked_candidates.is_empty() {
        return None;
    }

    ranked_candidates.sort_by(compare_ranked_candidates);
    ranked_candidates.first().map(|candidate| candidate.date)
}
