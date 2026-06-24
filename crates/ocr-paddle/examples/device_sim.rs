//! Simulate the on-device extraction on macOS and report quality.
//!
//! "Device behaviour" is exactly `ocr_paddle::process::process_image` (same Rust
//! code + ONNX models + ONNX Runtime CPU EP as the iOS app), so feeding it the
//! same image bytes reproduces the phone's result ~1:1. Use this to test the
//! pipeline against a corpus, or to diagnose a single exported capture.
//!
//! Two modes, same parser/scoring — the delta is purely OCR quality:
//!   live    (default): run the on-device ONNX models on `<stem>.jpg`
//!   --cached         : feed the desktop PaddleOCR `<stem>.ocr.json` detections
//!
//! Usage:
//!   cargo run -p ocr-paddle --example device_sim -- <image-or-dir> [--cached] [--models DIR] [--today YYYY-MM-DD] [--dump]
//!
//!   # on-device OCR over the private corpus:
//!   cargo run -p ocr-paddle --example device_sim -- ../beanbeaver-private-test/receipts_e2e
//!   # server OCR (cached) baseline over the same corpus, same parser:
//!   cargo run -p ocr-paddle --example device_sim -- ../beanbeaver-private-test/receipts_e2e --cached
//!
//! Compares against a sibling `<stem>.expected.json` when present (same schema
//! as tests/test_e2e_receipts.py: merchant / date / total / critical_items).

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use ocr_paddle::engine::OcrEngine;
use ocr_paddle::process::{process_image, resize_and_pad};
use receipt_core::ocr_transform::RawDetection;
use receipt_core::process::{process_receipt, ProcessedReceipt};
use receipt_core::receipt_categories::resolve_account_target;
use receipt_core::rules::default_parser_rule_layers;
use serde_json::Value;

/// Matches `ocr_paddle::process::OCR_IMAGE_PADDING` (the desktop server pads 50px
/// before OCR, so `.ocr.json` coords + dims are in that padded space).
const OCR_IMAGE_PADDING: i64 = 50;

fn main() {
    let mut path: Option<PathBuf> = None;
    let mut models = PathBuf::from("models");
    let mut today = (2026u16, 6u8, 21u8);
    let mut dump = false;
    let mut cached = false;
    let mut detcmp = false;

    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--models" => models = PathBuf::from(args.next().expect("--models needs a dir")),
            "--today" => today = parse_today(&args.next().expect("--today needs YYYY-MM-DD")),
            "--dump" => dump = true,
            "--cached" => cached = true,
            "--detcmp" => detcmp = true,
            _ => path = Some(PathBuf::from(a)),
        }
    }
    let path = path.expect("pass an image file or a directory");

    // Live mode needs the models; cached mode reads .ocr.json and skips them.
    let mut engine = if cached {
        None
    } else {
        let det = find_model(&models, "_det.onnx");
        let rec = find_model(&models, "_rec.onnx");
        let cls = find_model(&models, "_ori.onnx");
        eprintln!("det: {}\nrec: {}\ncls: {}", det.display(), rec.display(), cls.display());
        Some(OcrEngine::from_paths(det, rec, Some(cls)).expect("load models (pass --models DIR if not ./models)"))
    };

    let mapping: HashMap<String, String> = default_parser_rule_layers().account_mapping.into_iter().collect();
    let today = (today.0 as i32, today.1 as u32, today.2 as u32);

    if detcmp {
        run_detcmp(engine.as_mut().expect("engine for detcmp"), &path);
        return;
    }

    println!("mode: {}", if cached { "cached (desktop PaddleOCR)" } else { "live (on-device ONNX)" });
    if path.is_dir() {
        run_corpus(&mut engine, cached, &mapping, today, &path, dump);
    } else {
        run_single(&mut engine, cached, &mapping, today, &path, true);
    }
}

/// Compare our detection (final recognized lines) against PaddleOCR's `.ocr.json`
/// boxes in the same padded space — localizes missing vs duplicated lines.
fn run_detcmp(engine: &mut OcrEngine, path: &Path) {
    let mut jpgs: Vec<PathBuf> = std::fs::read_dir(path)
        .expect("read dir")
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "jpg") && p.with_extension("ocr.json").exists())
        .collect();
    jpgs.sort();

    println!("{:<40} {:>6} {:>6} {:>8} {:>8}", "fixture", "ours", "paddle", "box-rec", "txt-rec");
    let (mut sum_ours, mut sum_pad, mut sum_recall, mut sum_txt, mut n) = (0usize, 0usize, 0f64, 0f64, 0usize);
    for jpg in &jpgs {
        let img = image::open(jpg).expect("decode").to_rgb8();
        let dets = engine.recognize_image(&resize_and_pad(&img)).expect("detect");
        let our_texts: Vec<String> = dets.iter().map(|d| normalize_item(&d.text)).collect();
        let our_boxes: Vec<(f32, f32, f32, f32)> = dets
            .iter()
            .map(|d| {
                let (mut x0, mut y0, mut x1, mut y1) = (f32::MAX, f32::MAX, f32::MIN, f32::MIN);
                for p in &d.points {
                    x0 = x0.min(p[0]);
                    y0 = y0.min(p[1]);
                    x1 = x1.max(p[0]);
                    y1 = y1.max(p[1]);
                }
                (x0, y0, x1, y1)
            })
            .collect();

        let v: Value = serde_json::from_str(&std::fs::read_to_string(jpg.with_extension("ocr.json")).unwrap()).unwrap();
        let paddle: Vec<(f32, f32, String)> = v["detections"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|det| {
                let bbox = det[0].as_array()?;
                let cx = bbox.iter().filter_map(|p| p[0].as_f64()).sum::<f64>() / 4.0;
                let cy = bbox.iter().filter_map(|p| p[1].as_f64()).sum::<f64>() / 4.0;
                let text = det[1][0].as_str().unwrap_or_default().to_string();
                Some((cx as f32, cy as f32, text))
            })
            .collect();

        let covered = paddle
            .iter()
            .filter(|(cx, cy, _)| our_boxes.iter().any(|&(x0, y0, x1, y1)| *cx >= x0 && *cx <= x1 && *cy >= y0 && *cy <= y1))
            .count();
        // Text recall: paddle lines whose (normalized) text appears among ours.
        let txt_covered = paddle
            .iter()
            .filter(|(_, _, t)| {
                let nt = normalize_item(t);
                !nt.is_empty() && our_texts.iter().any(|o| o.contains(&nt) || nt.contains(o.as_str()))
            })
            .count();
        let recall = if paddle.is_empty() { 1.0 } else { covered as f64 / paddle.len() as f64 };
        let txt_recall = if paddle.is_empty() { 1.0 } else { txt_covered as f64 / paddle.len() as f64 };
        let name = jpg.file_stem().and_then(|s| s.to_str()).unwrap_or("?");
        println!("{name:<40} {:>6} {:>6} {:>7.0}% {:>7.0}%", dets.len(), paddle.len(), recall * 100.0, txt_recall * 100.0);
        sum_ours += dets.len();
        sum_pad += paddle.len();
        sum_recall += recall;
        sum_txt += txt_recall;
        n += 1;
    }

    println!("\n=== detcmp: {n} fixtures ===");
    println!("  our lines: {sum_ours}   paddle lines: {sum_pad}   (we find {:.0}% as many)", pct(sum_ours, sum_pad));
    println!("  box recall (paddle lines our boxes cover):   {:.0}%", if n > 0 { 100.0 * sum_recall / n as f64 } else { 0.0 });
    println!("  text recall (paddle lines we also read OK):  {:.0}%", if n > 0 { 100.0 * sum_txt / n as f64 } else { 0.0 });
}

/// Find the single `*<suffix>` ONNX in a model dir (works for both `mobile`
/// and `server` naming, e.g. `PP-OCRv5_server_det.onnx`).
fn find_model(dir: &Path, suffix: &str) -> PathBuf {
    std::fs::read_dir(dir)
        .unwrap_or_else(|_| panic!("model dir not found: {}", dir.display()))
        .filter_map(|e| e.ok().map(|e| e.path()))
        .find(|p| p.file_name().and_then(|n| n.to_str()).is_some_and(|n| n.ends_with(suffix)))
        .unwrap_or_else(|| panic!("no *{suffix} in {}", dir.display()))
}

/// Extract one receipt either live (ONNX on the jpg) or cached (desktop .ocr.json).
fn extract(
    engine: &mut Option<OcrEngine>,
    cached: bool,
    today: (i32, u32, u32),
    jpg: &Path,
) -> Option<ProcessedReceipt> {
    let name = jpg.file_stem().and_then(|s| s.to_str()).unwrap_or("image");
    let filename = format!("{name}.jpg");
    if cached {
        let ocr_path = jpg.with_extension("ocr.json");
        if !ocr_path.exists() {
            return None;
        }
        let v: Value = serde_json::from_str(&std::fs::read_to_string(&ocr_path).ok()?).ok()?;
        let (w, h) = (v.get("image_width")?.as_i64()?, v.get("image_height")?.as_i64()?);
        let mut raw = Vec::new();
        for det in v.get("detections")?.as_array()? {
            let arr = det.as_array()?;
            let points = arr[0]
                .as_array()?
                .iter()
                .filter_map(|p| {
                    let pa = p.as_array()?;
                    Some((pa[0].as_f64()?, pa[1].as_f64()?))
                })
                .collect();
            let tc = arr[1].as_array()?;
            raw.push(RawDetection {
                points,
                text: tc[0].as_str()?.to_string(),
                confidence: tc.get(1).and_then(Value::as_f64).unwrap_or(1.0),
            });
        }
        Some(process_receipt(raw, w, h, OCR_IMAGE_PADDING, &filename, None, today, "Liabilities:CreditCard", None))
    } else {
        let img = image::open(jpg).expect("decode image").to_rgb8();
        let engine = engine.as_mut().expect("engine for live mode");
        Some(process_image(engine, &img, &filename, today, "Liabilities:CreditCard", None).expect("process_image"))
    }
}

/// One image: print the full extraction (and diff if expected.json exists).
fn run_single(
    engine: &mut Option<OcrEngine>,
    cached: bool,
    mapping: &HashMap<String, String>,
    today: (i32, u32, u32),
    jpg: &Path,
    dump: bool,
) -> Option<FixtureScore> {
    let pr = extract(engine, cached, today, jpg)?;
    let d = &pr.parsed;
    let name = jpg.file_stem().and_then(|s| s.to_str()).unwrap_or("image");

    if dump {
        println!("── {name} ──");
        println!("merchant: {}", d.merchant);
        println!("date:     {}", fmt_date(d.date));
        println!("subtotal: {:?}  tax: {:?}  total: {}", d.subtotal, d.tax, d.total);
        println!("items ({}):", d.items.len());
        for it in &d.items {
            println!("  {:>9}  {:<32}  {}", it.price, it.description, it.category.as_deref().unwrap_or("-"));
        }
        println!("\n{}", pr.beancount);
    }

    let expected_path = jpg.with_extension("expected.json");
    if !expected_path.exists() {
        return None;
    }
    let expected: Value = serde_json::from_str(&std::fs::read_to_string(&expected_path).ok()?).ok()?;
    Some(score(name, &expected, d, mapping))
}

/// A directory: run every `<stem>.jpg` that has a `<stem>.expected.json`.
fn run_corpus(
    engine: &mut Option<OcrEngine>,
    cached: bool,
    mapping: &HashMap<String, String>,
    today: (i32, u32, u32),
    dir: &Path,
    dump: bool,
) {
    let mut jpgs: Vec<PathBuf> = std::fs::read_dir(dir)
        .expect("read dir")
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "jpg") && p.with_extension("expected.json").exists())
        .collect();
    jpgs.sort();

    let mut scores = Vec::new();
    for jpg in &jpgs {
        // In cached mode, fixtures without an .ocr.json are simply skipped.
        if cached && !jpg.with_extension("ocr.json").exists() {
            continue;
        }
        if let Some(s) = run_single(engine, cached, mapping, today, jpg, dump) {
            let mark = if s.is_fully_ok() { "✓" } else { "✗" };
            println!(
                "{mark} {:<42} {:<22} items {}/{}{}",
                s.name,
                format!("{} / {} / {}", trunc(&s.merchant, 14), s.date, s.total_got),
                s.items_ok,
                s.items_total,
                s.notes()
            );
            scores.push(s);
        }
    }

    let n = scores.len();
    if n == 0 {
        println!("(no usable <stem>.jpg + expected.json{} pairs in {})", if cached { " + ocr.json" } else { "" }, dir.display());
        return;
    }
    let merchant_ok = scores.iter().filter(|s| s.merchant_ok).count();
    let date_ok = scores.iter().filter(|s| s.date_ok).count();
    let total_ok = scores.iter().filter(|s| s.total_ok).count();
    let full_ok = scores.iter().filter(|s| s.is_fully_ok()).count();
    let items_ok: usize = scores.iter().map(|s| s.items_ok).sum();
    let items_total: usize = scores.iter().map(|s| s.items_total).sum();

    println!("\n=== device_sim summary ({}): {n} fixtures ===", if cached { "cached" } else { "live" });
    println!("  merchant : {merchant_ok}/{n}  ({:.0}%)", pct(merchant_ok, n));
    println!("  date     : {date_ok}/{n}  ({:.0}%)", pct(date_ok, n));
    println!("  total    : {total_ok}/{n}  ({:.0}%)", pct(total_ok, n));
    println!("  crit-items: {items_ok}/{items_total}  ({:.0}%)", pct(items_ok, items_total));
    println!("  fully OK : {full_ok}/{n}  ({:.0}%)", pct(full_ok, n));
}

struct FixtureScore {
    name: String,
    merchant: String,
    date: String,
    total_got: String,
    merchant_ok: bool,
    date_ok: bool,
    total_ok: bool,
    items_ok: usize,
    items_total: usize,
}

impl FixtureScore {
    fn is_fully_ok(&self) -> bool {
        self.merchant_ok && self.date_ok && self.total_ok && self.items_ok == self.items_total
    }
    fn notes(&self) -> String {
        let mut n = Vec::new();
        if !self.merchant_ok {
            n.push("merchant");
        }
        if !self.date_ok {
            n.push("date");
        }
        if !self.total_ok {
            n.push("total");
        }
        if n.is_empty() { String::new() } else { format!("   ✗ {}", n.join(",")) }
    }
}

fn score(
    name: &str,
    expected: &Value,
    d: &receipt_core::receipt_parser::ParsedReceiptData,
    mapping: &HashMap<String, String>,
) -> FixtureScore {
    let merchant_ok = match expected.get("merchant").and_then(Value::as_str) {
        None => true,
        Some(m) => {
            let any_of = expected
                .get("merchant_any_of")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(Value::as_str).collect::<Vec<_>>())
                .unwrap_or_default();
            expected.get("merchant_optional").and_then(Value::as_bool).unwrap_or(false)
                || merchant_matches(m, &d.merchant)
                || any_of.iter().any(|alt| merchant_matches(alt, &d.merchant))
        }
    };
    let date_ok = match expected.get("date").and_then(Value::as_str) {
        None => true,
        Some(dt) => d.date.map(fmt_ymd).as_deref() == Some(dt),
    };
    let total_ok = expected.get("total").and_then(Value::as_str).is_none_or(|t| price_matches(t, &d.total));

    let mut items_ok = 0;
    let mut items_total = 0;
    if let Some(items) = expected.get("critical_items").and_then(Value::as_array) {
        for ci in items {
            items_total += 1;
            let desc = ci.get("description").and_then(Value::as_str).unwrap_or_default();
            let price = ci.get("price").and_then(Value::as_str).unwrap_or_default();
            // Honor `category_optional` like the Python harness: when set, the
            // item only needs the right description+price; a category mismatch is
            // tolerated (these are items even the desktop pipeline mis-categorizes).
            let category_optional = ci.get("category_optional").and_then(Value::as_bool).unwrap_or(false);
            let category = if category_optional { None } else { ci.get("category").and_then(Value::as_str) };
            let matched: Vec<_> = d.items.iter().filter(|it| item_desc_matches(&it.description, desc)).collect();
            let ok = matched.iter().any(|it| price_matches(price, &it.price))
                && category.is_none_or(|cat| {
                    matched
                        .iter()
                        .filter(|it| price_matches(price, &it.price))
                        .any(|it| it.category.as_deref().is_some_and(|c| category_matches(cat, c, mapping)))
                });
            if ok {
                items_ok += 1;
            }
        }
    }

    FixtureScore {
        name: name.to_string(),
        merchant: d.merchant.clone(),
        date: fmt_date(d.date),
        total_got: d.total.clone(),
        merchant_ok,
        date_ok,
        total_ok,
        items_ok,
        items_total,
    }
}

// --- comparison helpers (shared semantics with tests/phase5_e2e.rs) ----------

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

fn merchant_matches(expected: &str, actual: &str) -> bool {
    let (e, a) = (normalize_merchant(expected), normalize_merchant(actual));
    if e.is_empty() || a.is_empty() {
        return false;
    }
    if a.contains(&e) || e.contains(&a) {
        return true;
    }
    let maxlen = e.len().max(a.len());
    (maxlen - levenshtein(e.as_bytes(), a.as_bytes())) as f64 / maxlen as f64 >= 0.85
}

fn price_matches(expected: &str, actual: &str) -> bool {
    match (expected.parse::<f64>(), actual.parse::<f64>()) {
        (Ok(e), Ok(a)) => (e - a).abs() < 0.005,
        _ => expected == actual,
    }
}

fn normalize_item(s: &str) -> String {
    let upper: String = s.to_uppercase().replace('O', "0");
    let stripped = match upper.find(|c: char| !c.is_ascii_digit()) {
        Some(i) if i > 0 && upper[i..].starts_with(char::is_whitespace) => upper[i..].trim_start(),
        _ => upper.as_str(),
    };
    stripped.chars().filter(|c| !c.is_whitespace()).collect()
}

fn item_desc_matches(actual: &str, expected: &str) -> bool {
    let (a, e) = (normalize_item(actual), normalize_item(expected));
    !e.is_empty() && (a.contains(&e) || e.contains(&a))
}

fn category_matches(expected: &str, actual: &str, mapping: &HashMap<String, String>) -> bool {
    let lc = |a: &str, b: &str| {
        let (a, b) = (a.to_uppercase(), b.to_uppercase());
        a.contains(&b) || b.contains(&a)
    };
    lc(expected, actual)
        || resolve_account_target(Some(expected), mapping, Some(expected))
            == resolve_account_target(Some(actual), mapping, Some(actual))
}

fn fmt_ymd((y, m, d): (i32, u32, u32)) -> String {
    format!("{y:04}-{m:02}-{d:02}")
}

fn fmt_date(d: Option<(i32, u32, u32)>) -> String {
    d.map(fmt_ymd).unwrap_or_else(|| "no-date".into())
}

fn parse_today(s: &str) -> (u16, u8, u8) {
    let p: Vec<&str> = s.split('-').collect();
    (p[0].parse().unwrap(), p[1].parse().unwrap(), p[2].parse().unwrap())
}

fn pct(a: usize, b: usize) -> f64 {
    if b == 0 { 100.0 } else { 100.0 * a as f64 / b as f64 }
}

fn trunc(s: &str, n: usize) -> String {
    if s.chars().count() <= n { s.to_string() } else { format!("{}…", s.chars().take(n - 1).collect::<String>()) }
}
