use regex::Regex;
use std::cmp::Ordering;
use std::sync::OnceLock;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct SimpleDate {
    pub(crate) year: i32,
    pub(crate) month: u32,
    pub(crate) day: u32,
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

pub(crate) fn extract_price_from_line(line: &str) -> Option<i64> {
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

pub(crate) fn extract_total(lines: &[String]) -> i64 {
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
        }
    }
    0
}

pub(crate) fn extract_tax(lines: &[String]) -> Option<i64> {
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

pub(crate) fn extract_subtotal(lines: &[String]) -> Option<i64> {
    for (idx, line) in lines.iter().enumerate() {
        let line_upper = line.to_ascii_uppercase();
        if line_upper.contains("SUBTOTAL") || line_upper.contains("SUB TOTAL") {
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

pub(crate) fn extract_date(
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
            let month_key = captures
                .get(1)
                .map(|m| m.as_str().to_ascii_lowercase())
                .unwrap_or_default();
            let month = match month_key.get(..3).unwrap_or("") {
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
            };
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
    }

    if ranked_candidates.is_empty() {
        return None;
    }

    ranked_candidates.sort_by(compare_ranked_candidates);
    ranked_candidates.first().map(|candidate| candidate.date)
}
