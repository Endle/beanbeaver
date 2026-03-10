use regex::Regex;
use std::cmp::Ordering;
use std::collections::HashSet;
use std::sync::OnceLock;

const FUZZY_THRESHOLD_SHORT: f64 = 0.75;
const FUZZY_THRESHOLD_MEDIUM: f64 = 0.80;
const FUZZY_THRESHOLD_LONG: f64 = 0.70;

#[derive(Clone, Debug)]
pub(crate) struct CategoryRule {
    pub(crate) keywords: Vec<String>,
    pub(crate) category: Option<String>,
    pub(crate) tags: Vec<String>,
    pub(crate) priority: i32,
}

#[derive(Clone, Debug)]
pub(crate) struct CategoryRuleLayers {
    pub(crate) rules: Vec<CategoryRule>,
    pub(crate) exact_only_keywords: HashSet<String>,
}

#[derive(Clone, Debug)]
pub(crate) struct RuleMatch {
    pub(crate) category: Option<String>,
    pub(crate) tags: Vec<String>,
    pub(crate) matched_keyword: String,
    pub(crate) priority: i32,
    pub(crate) keyword_length: usize,
    pub(crate) is_exact: bool,
    pub(crate) rule_index: usize,
}

fn re_word_token() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[A-Z0-9]+").unwrap())
}

fn re_whitespace() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[^A-Z0-9]+").unwrap())
}

fn bigram_similarity(s1: &str, s2: &str) -> f64 {
    if s1.len() < 2 {
        return if s2.contains(s1) { 1.0 } else { 0.0 };
    }

    let bigrams1: HashSet<String> = s1
        .as_bytes()
        .windows(2)
        .map(|window| String::from_utf8_lossy(window).into_owned())
        .collect();
    let bigrams2: HashSet<String> = s2
        .as_bytes()
        .windows(2)
        .map(|window| String::from_utf8_lossy(window).into_owned())
        .collect();

    if bigrams1.is_empty() {
        return 0.0;
    }
    bigrams1.intersection(&bigrams2).count() as f64 / bigrams1.len() as f64
}

fn get_threshold(keyword_len: usize) -> f64 {
    if keyword_len <= 4 {
        FUZZY_THRESHOLD_SHORT
    } else if keyword_len <= 6 {
        FUZZY_THRESHOLD_MEDIUM
    } else {
        FUZZY_THRESHOLD_LONG
    }
}

fn normalize_ocr_confusables(text: &str) -> String {
    text.chars()
        .map(|ch| match ch {
            '0' | 'D' => 'O',
            _ => ch,
        })
        .collect()
}

fn contains_with_single_char_noise(keyword: &str, description: &str) -> Option<usize> {
    let kw_tokens: Vec<&str> = keyword
        .split_whitespace()
        .filter(|token| !token.is_empty())
        .collect();
    if kw_tokens.len() < 2 {
        return None;
    }

    let normalized_desc = re_whitespace()
        .replace_all(&description.to_ascii_uppercase(), " ")
        .trim()
        .to_string();
    if normalized_desc.is_empty() {
        return None;
    }

    let mut pattern = format!(r"\b{}\b", regex::escape(kw_tokens[0]));
    for token in kw_tokens.iter().skip(1) {
        pattern.push_str(r"(?:\s+[A-Z0-9]\b)?\s+\b");
        pattern.push_str(&regex::escape(token));
        pattern.push_str(r"\b");
    }

    Regex::new(&pattern)
        .ok()
        .and_then(|regex| regex.find(&normalized_desc).map(|matched| matched.start()))
}

fn compact_without_spaces(value: &str) -> String {
    value.chars().filter(|ch| !ch.is_whitespace()).collect()
}

fn fuzzy_contains(keyword: &str, description: &str, threshold: Option<f64>) -> (bool, isize, bool) {
    let desc_raw = description.to_ascii_uppercase();
    let kw_raw = keyword.trim().to_ascii_uppercase();
    let desc_conf_raw = normalize_ocr_confusables(&desc_raw);
    let kw_conf_raw = normalize_ocr_confusables(&kw_raw);
    let exact_only = threshold.is_some_and(|value| value >= 1.0);

    let kw_len_raw = kw_raw.chars().filter(|ch| !ch.is_whitespace()).count();
    if kw_len_raw <= 3 {
        let pattern = format!(r"\b{}\b", regex::escape(&kw_raw));
        if let Ok(regex) = Regex::new(&pattern) {
            if let Some(found) = regex.find(&desc_raw) {
                return (true, found.start() as isize, true);
            }
        }
        if !exact_only {
            for token_match in re_word_token().find_iter(&desc_raw) {
                if normalize_ocr_confusables(token_match.as_str()) == kw_conf_raw {
                    return (true, token_match.start() as isize, true);
                }
            }
        }
        return (false, -1, false);
    }

    let desc = compact_without_spaces(&desc_raw);
    let kw = compact_without_spaces(&kw_raw);
    let desc_conf = compact_without_spaces(&desc_conf_raw);
    let kw_conf = compact_without_spaces(&kw_conf_raw);

    if let Some(position) = desc.find(&kw) {
        return (true, position as isize, true);
    }
    if !exact_only {
        if let Some(position) = desc_conf.find(&kw_conf) {
            return (true, position as isize, true);
        }
    }

    if let Some(position) = contains_with_single_char_noise(&kw_raw, &desc_raw) {
        return (true, position as isize, true);
    }
    if !exact_only {
        if let Some(position) = contains_with_single_char_noise(&kw_conf_raw, &desc_conf_raw) {
            return (true, position as isize, true);
        }
    }

    let keyword_len = kw.chars().count();
    let threshold = threshold.unwrap_or_else(|| get_threshold(keyword_len));
    if threshold >= 1.0 {
        return (false, -1, false);
    }

    let desc_chars: Vec<char> = desc_conf.chars().collect();
    let kw_chars: Vec<char> = kw_conf.chars().collect();
    let window_size = keyword_len + 1;
    let mut best_similarity = 0.0;
    let mut best_position = -1;

    for start in 0..=(desc_chars.len().saturating_sub(keyword_len)) {
        let end = (start + window_size).min(desc_chars.len());
        let window: String = desc_chars[start..end].iter().collect();
        let keyword_string: String = kw_chars.iter().collect();
        let similarity = bigram_similarity(&keyword_string, &window);
        if similarity > best_similarity {
            best_similarity = similarity;
            best_position = start as isize;
        }
    }

    if best_similarity >= threshold {
        (true, best_position, false)
    } else {
        (false, -1, false)
    }
}

pub(crate) fn find_all_matches(description: &str, rule_layers: &CategoryRuleLayers) -> Vec<RuleMatch> {
    let mut matches = Vec::new();

    for (rule_index, rule) in rule_layers.rules.iter().enumerate() {
        for keyword in &rule.keywords {
            let threshold = if rule_layers.exact_only_keywords.contains(keyword) {
                Some(1.0)
            } else {
                None
            };
            let (matched, _, is_exact) = fuzzy_contains(keyword, description, threshold);
            if matched {
                matches.push(RuleMatch {
                    category: rule.category.clone(),
                    tags: rule.tags.clone(),
                    matched_keyword: keyword.clone(),
                    priority: rule.priority,
                    keyword_length: keyword.chars().filter(|ch| !ch.is_whitespace()).count(),
                    is_exact,
                    rule_index,
                });
                break;
            }
        }
    }

    matches
}

fn compare_match_rank(left: &RuleMatch, right: &RuleMatch) -> Ordering {
    left.priority
        .cmp(&right.priority)
        .then_with(|| (left.is_exact as u8).cmp(&(right.is_exact as u8)))
        .then_with(|| left.keyword_length.cmp(&right.keyword_length))
        .then_with(|| right.rule_index.cmp(&left.rule_index))
}

pub(crate) fn classify_item_key(
    description: &str,
    rule_layers: &CategoryRuleLayers,
    default: Option<String>,
) -> Option<String> {
    let matches = find_all_matches(description, rule_layers);
    let best = matches
        .into_iter()
        .filter(|matched| matched.category.is_some())
        .max_by(|left, right| compare_match_rank(left, right));
    best.and_then(|matched| matched.category).or(default)
}

pub(crate) fn classify_item_tags(description: &str, rule_layers: &CategoryRuleLayers) -> Vec<String> {
    let matches = find_all_matches(description, rule_layers);
    let mut tags = Vec::new();
    let mut seen = HashSet::new();

    for matched in matches {
        for tag in matched.tags {
            if seen.insert(tag.clone()) {
                tags.push(tag);
            }
        }
    }

    tags
}

pub(crate) fn sorted_matches_for_debug(description: &str, rule_layers: &CategoryRuleLayers) -> Vec<RuleMatch> {
    let mut matches = find_all_matches(description, rule_layers);
    matches.sort_by(|left, right| compare_match_rank(right, left));
    matches
}
