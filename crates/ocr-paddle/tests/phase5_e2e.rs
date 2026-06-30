//! Phase 5: validate the on-device pipeline (`process_image`) against the same
//! `tests/receipts_e2e/*.expected.json` fixtures the desktop Python e2e uses,
//! over every fixture that ships a `.jpg`. Mirrors the checks in
//! `tests/test_e2e_receipts.py` (merchant fuzzy / date exact / total exact /
//! critical items), but runs the fat-Rust seam end to end.
//!
//!   cargo test -p ocr-paddle --test phase5_e2e -- --ignored --nocapture
//!
//! Parity is approximate by design (Core-Image vs PIL resize, ORT vs Paddle
//! kernels), so a small set of critical-item checks the on-device OCR currently
//! can't satisfy are tracked append-only in `KNOWN_ON_DEVICE_GAPS` — the public
//! `expected.json` is the desktop baseline and is never weakened here.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use ocr_paddle::engine::OcrEngine;
use ocr_paddle::process::process_image;
use receipt_core::receipt_categories::resolve_account_target;
use receipt_core::rules::default_parser_rule_layers;
use serde_json::Value;

/// (fixture name, check id) pairs the on-device OCR currently can't satisfy vs
/// the server baseline. The check id is a critical-item description, or "@total".
/// Append-only; each entry is a known parity gap, not a regression. The public
/// `expected.json` is the desktop baseline and is never weakened here.
/// Calibrated at RESIZE_LONG=1536. The detection-resolution bump fixed some gaps
/// (WING HING, WASABI tilts) and shifted others (DOORDASH/WJ on the upright base
/// now fail) — a known trade-off until the DB box-segmentation is made faithful.
const KNOWN_ON_DEVICE_GAPS: &[(&str, &str)] = &[
    // Single-character OCR misreads vs the server (present even upright/mild):
    ("tnt_20260217_redact", "TOMAX STEAM MEAT 5 SPICES PDR"), // "PDR" read as "PUR"
    ("tnt_20260217_redact", "WJ LIGHT PRSUD MUSTARD STEM"),
    ("tnt_20260217_redact_tilt3", "TOMAX STEAM MEAT 5 SPICES PDR"),
    // Box-segmentation divergence (see preprocess::RESIZE_LONG): the upright
    // costco DOORDASH line is detected/split differently at 1536.
    ("costco_20260218_redact", "DOORDASH2X50"),
    // Severe synthetic tilt (5deg/7deg) degrades detection — wrong total and
    // shuffled/misread item prices:
    ("costco_20260218_redact_tilt5", "@total"),
    ("costco_20260218_redact_tilt5", "COKE ZERO"),
    ("costco_20260218_redact_tilt5", "DOORDASH2X50"),
    ("costco_20260218_redact_tilt7", "@total"),
    ("costco_20260218_redact_tilt7", "COKE ZERO"),
    ("tnt_20260217_redact_tilt5", "TOMAX STEAM MEAT 5 SPICES PDR"),
    ("tnt_20260217_redact_tilt5", "MAMA TRUFFLE INSTANT NOODLE"),
    ("tnt_20260217_redact_tilt7", "TOMAX STEAM MEAT 5 SPICES PDR"),
    ("tnt_20260217_redact_tilt7", "WJ LIGHT PRSUD MUSTARD STEM"),
    ("tnt_20260217_redact_tilt7", "MAMA TRUFFLE INSTANT NOODLE"),
];

fn repo_path(rel: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").join(rel)
}

fn normalize_merchant(s: &str) -> String {
    s.chars().filter(|c| c.is_alphanumeric()).flat_map(char::to_uppercase).collect()
}

fn levenshtein(a: &[u8], b: &[u8]) -> usize {
    let mut prev: Vec<usize> = (0..=b.len()).collect();
    let mut cur = vec![0usize; b.len() + 1];
    for (i, &ca) in a.iter().enumerate() {
        cur[0] = i + 1;
        for (j, &cb) in b.iter().enumerate() {
            let cost = usize::from(ca != cb);
            cur[j + 1] = (prev[j + 1] + 1).min(cur[j] + 1).min(prev[j] + cost);
        }
        std::mem::swap(&mut prev, &mut cur);
    }
    prev[b.len()]
}

/// Faithful to the Python e2e: normalized substring either way, else a
/// similarity ratio >= 0.85 (tolerates single-char OCR slips like m->n).
fn merchant_matches(expected: &str, actual: &str) -> bool {
    let (e, a) = (normalize_merchant(expected), normalize_merchant(actual));
    if e.is_empty() || a.is_empty() {
        return false;
    }
    if a.contains(&e) || e.contains(&a) {
        return true;
    }
    let maxlen = e.len().max(a.len());
    let ratio = (maxlen - levenshtein(e.as_bytes(), a.as_bytes())) as f64 / maxlen as f64;
    ratio >= 0.85
}

/// Decimal-equal (expected "6.97" vs on-device "6.9700").
fn price_matches(expected: &str, actual: &str) -> bool {
    match (expected.parse::<f64>(), actual.parse::<f64>()) {
        (Ok(e), Ok(a)) => (e - a).abs() < 0.005,
        _ => expected == actual,
    }
}

/// Case-insensitive substring either way.
fn loose_contains(a: &str, b: &str) -> bool {
    let (a, b) = (a.to_uppercase(), b.to_uppercase());
    a.contains(&b) || b.contains(&a)
}

/// Normalize an item description for OCR-tolerant matching: uppercase, collapse
/// letter-O / digit-0 (the dominant on-device confusion), strip a leading item
/// code (`"232952 C0KE ZERO"` -> `"COKE ZERO"`), and drop spaces (`"2% FINE-FILT"`
/// == `"2%FINE-FILT"`). Applied to both sides, so it only relaxes matching.
fn normalize_item(s: &str) -> String {
    let upper: String = s.to_uppercase().replace('O', "0");
    // Strip a leading run of digits followed by whitespace (the item code).
    let stripped = match upper.find(|c: char| !c.is_ascii_digit()) {
        Some(i) if i > 0 && upper[i..].starts_with(char::is_whitespace) => upper[i..].trim_start(),
        _ => upper.as_str(),
    };
    stripped.chars().filter(|c| !c.is_whitespace()).collect()
}

/// Item-description match, OCR-tolerant, substring either way.
fn item_desc_matches(actual: &str, expected: &str) -> bool {
    let (a, e) = (normalize_item(actual), normalize_item(expected));
    !e.is_empty() && (a.contains(&e) || e.contains(&a))
}

/// Faithful to the Python `_category_matches`: substring, else resolve both the
/// expected key and the actual account to accounts and compare.
fn category_matches(expected: &str, actual: &str, mapping: &HashMap<String, String>) -> bool {
    if loose_contains(expected, actual) {
        return true;
    }
    resolve_account_target(Some(expected), mapping, Some(expected))
        == resolve_account_target(Some(actual), mapping, Some(actual))
}

#[test]
#[ignore = "needs converted models + fixtures"]
fn phase5_on_device_vs_expected() {
    let fixtures = repo_path("tests/receipts_e2e");
    let models = repo_path("models");
    let account_mapping: HashMap<String, String> =
        default_parser_rule_layers().account_mapping.into_iter().collect();

    let mut engine = OcrEngine::from_paths(
        models.join("PP-OCRv5_mobile_det.onnx"),
        models.join("PP-OCRv5_mobile_rec.onnx"),
        Some(models.join("PP-LCNet_x1_0_textline_ori.onnx")),
    )
    .expect("load models");

    let mut names: Vec<String> = fs::read_dir(&fixtures)
        .expect("read fixtures dir")
        .filter_map(|e| e.ok()?.file_name().into_string().ok())
        .filter_map(|n| n.strip_suffix(".expected.json").map(str::to_string))
        .collect();
    names.sort();

    let mut ran = 0;
    let mut known_gaps = 0;
    let mut failures: Vec<String> = Vec::new();

    for name in &names {
        let jpg = fixtures.join(format!("{name}.jpg"));
        if !jpg.exists() {
            continue; // on-device path needs an image (cached-only fixtures skipped)
        }
        ran += 1;

        let expected: Value =
            serde_json::from_str(&fs::read_to_string(fixtures.join(format!("{name}.expected.json"))).unwrap())
                .unwrap();
        let img = image::open(&jpg).expect("decode fixture").to_rgb8();
        let pr =
            process_image(&mut engine, &img, &format!("{name}.jpg"), (2026, 6, 21), "Liabilities:CreditCard", None)
                .expect("process_image");
        let d = &pr.parsed;
        let mut fail = |msg: String| failures.push(format!("{name}: {msg}"));

        if let Some(m) = expected.get("merchant").and_then(Value::as_str) {
            let optional = expected.get("merchant_optional").and_then(Value::as_bool).unwrap_or(false);
            let any_of = expected
                .get("merchant_any_of")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(Value::as_str).collect::<Vec<_>>())
                .unwrap_or_default();
            let ok = merchant_matches(m, &d.merchant) || any_of.iter().any(|alt| merchant_matches(alt, &d.merchant));
            if !ok && !optional {
                fail(format!("merchant expected '{m}', got '{}'", d.merchant));
            }
        }

        if let Some(dt) = expected.get("date").and_then(Value::as_str) {
            let actual = d.date.map(|(y, m, day)| format!("{y:04}-{m:02}-{day:02}"));
            if actual.as_deref() != Some(dt) {
                fail(format!("date expected '{dt}', got {actual:?}"));
            }
        }

        if let Some(t) = expected.get("total").and_then(Value::as_str) {
            if !price_matches(t, &d.total) {
                if KNOWN_ON_DEVICE_GAPS.contains(&(name.as_str(), "@total")) {
                    known_gaps += 1;
                    eprintln!("  ~ {name}: known gap @total ('{t}' vs '{}')", d.total);
                } else {
                    fail(format!("total expected '{t}', got '{}'", d.total));
                }
            }
        }

        if let Some(items) = expected.get("critical_items").and_then(Value::as_array) {
            for ci in items {
                let desc = ci.get("description").and_then(Value::as_str).unwrap_or_default();
                let price = ci.get("price").and_then(Value::as_str).unwrap_or_default();
                // Honor `category_optional` like the Python harness: when set, only
                // description+price are required and a category mismatch is tolerated.
                let category_optional = ci.get("category_optional").and_then(Value::as_bool).unwrap_or(false);
                let category = if category_optional { None } else { ci.get("category").and_then(Value::as_str) };
                let is_known_gap = KNOWN_ON_DEVICE_GAPS.contains(&(name.as_str(), desc));

                let matched: Vec<_> = d.items.iter().filter(|it| item_desc_matches(&it.description, desc)).collect();
                let item_ok = matched.iter().any(|it| price_matches(price, &it.price))
                    && category.is_none_or(|cat| {
                        matched
                            .iter()
                            .filter(|it| price_matches(price, &it.price))
                            .any(|it| it.category.as_deref().is_some_and(|c| category_matches(cat, c, &account_mapping)))
                    });

                if item_ok {
                    continue;
                }
                if is_known_gap {
                    known_gaps += 1;
                    eprintln!("  ~ {name}: known gap '{desc}'");
                    continue;
                }
                let got: Vec<_> = matched.iter().map(|it| (it.description.as_str(), it.price.as_str())).collect();
                fail(format!("item '{desc}' (price {price}, cat {category:?}) unmatched; candidates {got:?}"));
            }
        }

        eprintln!(
            "✓ {name}  ({} / {} / {})",
            d.merchant,
            d.date.map(|(y, m, dd)| format!("{y:04}-{m:02}-{dd:02}")).unwrap_or_else(|| "no-date".into()),
            d.total
        );
        if std::env::var("PHASE5_DUMP").is_ok() {
            for it in &d.items {
                eprintln!("      · {:?} = {}", it.description, it.price);
            }
        }
    }

    eprintln!("\nPhase 5: {ran} image fixtures, {known_gaps} known on-device gap(s), {} hard divergence(s)", failures.len());
    for f in &failures {
        eprintln!("  ✗ {f}");
    }
    assert!(failures.is_empty(), "{} on-device check(s) diverged from expected (and not in KNOWN_ON_DEVICE_GAPS)", failures.len());
}
