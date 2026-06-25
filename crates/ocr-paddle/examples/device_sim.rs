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

use ocr_paddle::db_postprocess::{boxes_from_bitmap, DbConfig};
use ocr_paddle::detect::Detector;
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
    let mut attrib = false;
    let mut reccached = false;
    let mut by_merchant = false;
    let mut probdump: Option<PathBuf> = None;

    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--models" => models = PathBuf::from(args.next().expect("--models needs a dir")),
            "--today" => today = parse_today(&args.next().expect("--today needs YYYY-MM-DD")),
            "--dump" => dump = true,
            "--cached" => cached = true,
            "--detcmp" => detcmp = true,
            "--attrib" => attrib = true,
            "--reccached" => reccached = true,
            "--by-merchant" => by_merchant = true,
            "--probdump" => probdump = Some(PathBuf::from(args.next().expect("--probdump needs an out dir"))),
            _ => path = Some(PathBuf::from(a)),
        }
    }
    let path = path.expect("pass an image file or a directory");

    // Dump the raw DB prob map + our boxes for an external mask-vs-contour diff
    // (see scripts/contour_cmp.py). Needs only the detection model.
    if let Some(outdir) = probdump {
        run_probdump(&models, &path, &outdir);
        return;
    }

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

    if attrib {
        run_attrib(engine.as_mut().expect("engine for attrib"), &path);
        return;
    }

    if reccached {
        run_reccached(engine.as_mut().expect("engine for reccached"), &mapping, today, &path);
        return;
    }

    println!("mode: {}", if cached { "cached (desktop PaddleOCR)" } else { "live (on-device ONNX)" });
    if path.is_dir() {
        run_corpus(&mut engine, cached, &mapping, today, &path, dump, by_merchant);
    } else {
        run_single(&mut engine, cached, &mapping, today, &path, true);
    }
}

/// Dump the raw DB probability map + our resulting boxes for one image, so the
/// SAME prob map can be fed through PaddleOCR's reference DBPostProcess in Python
/// (`scripts/contour_cmp.py`). Any box difference is then purely the
/// contour/min-rect/unclip algorithm (imageproc Suzuki vs OpenCV), isolated from
/// the upstream mask/model/preprocessing. Coords are in the padded space that
/// `process_image` (and `.ocr.json`) use.
fn run_probdump(models: &Path, img_path: &Path, outdir: &Path) {
    std::fs::create_dir_all(outdir).expect("create probdump dir");
    let det_model = find_model(models, "_det.onnx");
    let mut det = Detector::from_path(&det_model).expect("load det model");

    let img = image::open(img_path).expect("decode image").to_rgb8();
    let padded = resize_and_pad(&img);
    let p = det.prob_map(&padded).expect("prob_map");

    // Raw prob map: little-endian f32, row-major h*w.
    let mut bytes = Vec::with_capacity(p.prob.len() * 4);
    for v in &p.prob {
        bytes.extend_from_slice(&v.to_le_bytes());
    }
    std::fs::write(outdir.join("prob.f32"), &bytes).expect("write prob.f32");

    let cfg = DbConfig::default();
    let meta = serde_json::json!({
        "h": p.h, "w": p.w,
        "dest_w": p.orig_w, "dest_h": p.orig_h,   // padded-image dims (mapping target)
        "ratio_w": p.ratio_w, "ratio_h": p.ratio_h,
        "thresh": cfg.thresh, "box_thresh": cfg.box_thresh,
        "unclip_ratio": cfg.unclip_ratio, "max_candidates": cfg.max_candidates,
        "image": img_path.file_name().and_then(|s| s.to_str()),
    });
    std::fs::write(outdir.join("meta.json"), serde_json::to_string_pretty(&meta).unwrap())
        .expect("write meta.json");

    let quads = boxes_from_bitmap(&p.prob, p.h, p.w, p.orig_w, p.orig_h, p.ratio_w, p.ratio_h, &cfg);
    let boxes: Vec<[[f32; 2]; 4]> = quads.iter().map(|q| q.points).collect();
    std::fs::write(outdir.join("ours_boxes.json"), serde_json::to_string(&boxes).unwrap())
        .expect("write ours_boxes.json");

    println!(
        "probdump: {} boxes | prob {}x{} | dest {}x{} -> {}",
        boxes.len(), p.w, p.h, p.orig_w as u32, p.orig_h as u32, outdir.display()
    );
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

/// Per-field tally of why live failures happened, vs the desktop OCR (`.ocr.json`)
/// as the "winnable" reference and `expected.json` as ground truth.
#[derive(Clone, Copy, Debug, Default)]
struct CauseCounts {
    miss: usize,         // line not detected at all (desktop found it, we didn't)
    bad_crop: usize,     // detected but box mispositioned -> garbled crop
    true_rec: usize,     // box well-placed but text still wrong -> recognition bug
    pairing: usize,      // text read OK, parser produced wrong/no value (mis-pair / price misread)
    desk_missing: usize, // desktop OCR also lacked it (not our gap)
    unknown: usize,      // couldn't locate a desktop reference line (heuristic miss)
}

#[derive(Clone, Copy, Debug)]
enum Cause {
    Miss,
    BadCrop,
    TrueRec,
    Pairing,
    DeskMissing,
    Unknown,
}

fn bump(c: &mut CauseCounts, cause: Cause) {
    match cause {
        Cause::Miss => c.miss += 1,
        Cause::BadCrop => c.bad_crop += 1,
        Cause::TrueRec => c.true_rec += 1,
        Cause::Pairing => c.pairing += 1,
        Cause::DeskMissing => c.desk_missing += 1,
        Cause::Unknown => c.unknown += 1,
    }
}

type Bx = (f32, f32, f32, f32);

fn det_bbox(points: &[[f32; 2]; 4]) -> Bx {
    let (mut x0, mut y0, mut x1, mut y1) = (f32::MAX, f32::MAX, f32::MIN, f32::MIN);
    for p in points {
        x0 = x0.min(p[0]);
        y0 = y0.min(p[1]);
        x1 = x1.max(p[0]);
        y1 = y1.max(p[1]);
    }
    (x0, y0, x1, y1)
}

fn iou(a: Bx, b: Bx) -> f32 {
    let inter = (a.2.min(b.2) - a.0.max(b.0)).max(0.0) * (a.3.min(b.3) - a.1.max(b.1)).max(0.0);
    let area = |x: Bx| (x.2 - x.0).max(0.0) * (x.3 - x.1).max(0.0);
    let uni = area(a) + area(b) - inter;
    if uni <= 0.0 { 0.0 } else { inter / uni }
}

fn digits(s: &str) -> String {
    s.chars().filter(|c| c.is_ascii_digit()).collect()
}

/// Given the desktop "ground-truth" box for a failed line, classify what the live
/// pipeline did with it. `recognized` = some overlapping live box already read the
/// line's key text correctly, so the parser (not OCR) is at fault.
fn classify_against_deskbox(desk_box: Bx, live: &[(Bx, String)], recognized: bool) -> Cause {
    if recognized {
        return Cause::Pairing;
    }
    let best = live
        .iter()
        .map(|(b, _)| iou(*b, desk_box))
        .fold(0.0f32, f32::max);
    if best <= 0.05 {
        Cause::Miss
    } else if best > 0.5 {
        Cause::TrueRec
    } else {
        Cause::BadCrop
    }
}

/// `--attrib`: for every live scoring failure, attribute it to a pipeline stage by
/// diffing live detections against the desktop `.ocr.json` and `expected.json`.
/// Sizes the detection-recall vs box-position vs recognition buckets. Set
/// `ATTRIB_V=1` for a per-failure line.
fn run_attrib(engine: &mut OcrEngine, path: &Path) {
    let mut jpgs: Vec<PathBuf> = std::fs::read_dir(path)
        .expect("read dir")
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            p.extension().is_some_and(|x| x == "jpg")
                && p.with_extension("ocr.json").exists()
                && p.with_extension("expected.json").exists()
        })
        .collect();
    jpgs.sort();

    let today = (2026i32, 6u32, 21u32);
    let verbose = std::env::var("ATTRIB_V").is_ok();
    let (mut date_c, mut total_c, mut item_c) =
        (CauseCounts::default(), CauseCounts::default(), CauseCounts::default());
    let (mut date_fail, mut total_fail, mut item_miss) = (0usize, 0usize, 0usize);

    for jpg in &jpgs {
        let name = jpg.file_stem().and_then(|s| s.to_str()).unwrap_or("?");
        let img = image::open(jpg).expect("decode").to_rgb8();

        // Live detections (text + box, padded space — what process_image sees).
        let live_raw = engine.recognize_image(&resize_and_pad(&img)).expect("detect");
        let live: Vec<(Bx, String)> = live_raw.iter().map(|d| (det_bbox(&d.points), d.text.clone())).collect();

        // Desktop detections from .ocr.json (also padded space).
        let v: Value = serde_json::from_str(&std::fs::read_to_string(jpg.with_extension("ocr.json")).unwrap()).unwrap();
        let desk: Vec<(Bx, String)> = v["detections"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|det| {
                let bbox = det[0].as_array()?;
                let (mut x0, mut y0, mut x1, mut y1) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
                for p in bbox {
                    let pa = p.as_array()?;
                    let (x, y) = (pa[0].as_f64()?, pa[1].as_f64()?);
                    x0 = x0.min(x);
                    y0 = y0.min(y);
                    x1 = x1.max(x);
                    y1 = y1.max(y);
                }
                let text = det[1][0].as_str().unwrap_or_default().to_string();
                Some(((x0 as f32, y0 as f32, x1 as f32, y1 as f32), text))
            })
            .collect();

        // Live parsed result.
        let pr = process_image(engine, &img, &format!("{name}.jpg"), today, "Liabilities:CreditCard", None).expect("process_image");
        let d = &pr.parsed;
        let expected: Value = serde_json::from_str(&std::fs::read_to_string(jpg.with_extension("expected.json")).unwrap()).unwrap();

        // ---- items ----
        if let Some(items) = expected.get("critical_items").and_then(Value::as_array) {
            for ci in items {
                let desc = ci.get("description").and_then(Value::as_str).unwrap_or_default();
                let price = ci.get("price").and_then(Value::as_str).unwrap_or_default();
                let parser_ok = d.items.iter().any(|it| item_desc_matches(&it.description, desc) && price_matches(price, &it.price));
                if parser_ok {
                    continue;
                }
                item_miss += 1;
                let key = normalize_item(desc);
                let key_hit = |t: &str| {
                    let nt = normalize_item(t);
                    !nt.is_empty() && (nt.contains(&key) || key.contains(&nt))
                };
                let cause = match desk.iter().find(|(_, t)| key_hit(t)).map(|(b, _)| *b) {
                    None => Cause::DeskMissing,
                    Some(b) => {
                        let recognized = live.iter().any(|(lb, t)| iou(*lb, b) > 0.05 && key_hit(t));
                        classify_against_deskbox(b, &live, recognized)
                    }
                };
                bump(&mut item_c, cause);
                if verbose {
                    eprintln!("  [{name}] ITEM '{desc}' @{price} -> {cause:?}");
                }
            }
        }

        // ---- date ----
        if let Some(exp) = expected.get("date").and_then(Value::as_str) {
            let got = d.date.map(fmt_ymd);
            if got.as_deref() != Some(exp) {
                date_fail += 1;
                let year = &exp[0..4];
                let cause = match desk.iter().find(|(_, t)| t.contains(year)) {
                    None => Cause::Unknown,
                    Some((b, dt)) => {
                        let key = digits(dt);
                        let recognized = live.iter().any(|(_, t)| !key.is_empty() && digits(t).contains(&key));
                        classify_against_deskbox(*b, &live, recognized)
                    }
                };
                bump(&mut date_c, cause);
                if verbose {
                    eprintln!("  [{name}] DATE exp {exp} got {got:?} -> {cause:?}");
                }
            }
        }

        // ---- total ----
        if let Some(exp) = expected.get("total").and_then(Value::as_str) {
            if !price_matches(exp, &d.total) {
                total_fail += 1;
                let key = digits(exp);
                let cause = match desk.iter().find(|(_, t)| !key.is_empty() && digits(t).contains(&key)) {
                    None => Cause::Unknown,
                    Some((b, _)) => {
                        let recognized = live.iter().any(|(_, t)| digits(t).contains(&key));
                        classify_against_deskbox(*b, &live, recognized)
                    }
                };
                bump(&mut total_c, cause);
                if verbose {
                    eprintln!("  [{name}] TOTAL exp {exp} got {} -> {cause:?}", d.total);
                }
            }
        }
    }

    let hdr = format!(
        "{:<7} {:>6}   {:>8} {:>8} {:>8} {:>8} {:>9} {:>7}",
        "field", "failed", "det-miss", "bad-crop", "true-rec", "pairing", "desk-miss", "unknown"
    );
    let row = |label: &str, n: usize, c: &CauseCounts| {
        println!(
            "{label:<7} {n:>6}   {:>8} {:>8} {:>8} {:>8} {:>9} {:>7}",
            c.miss, c.bad_crop, c.true_rec, c.pairing, c.desk_missing, c.unknown
        );
    };
    println!("\n=== failure attribution (live, {} fixtures) ===", jpgs.len());
    println!("{hdr}");
    row("date", date_fail, &date_c);
    row("total", total_fail, &total_c);
    row("items", item_miss, &item_c);
    println!(
        "\nlegend: det-miss=line not detected | bad-crop=detected, box mispositioned (->bad crop) |"
    );
    println!(
        "        true-rec=box well-placed, text still wrong (rec bug) | pairing=text read OK, parser mis-paired/price-misread |"
    );
    println!("        desk-miss=desktop OCR also lacked it (not our gap) | unknown=no desktop reference line located");
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
#[allow(clippy::too_many_arguments)]
fn run_corpus(
    engine: &mut Option<OcrEngine>,
    cached: bool,
    mapping: &HashMap<String, String>,
    today: (i32, u32, u32),
    dir: &Path,
    dump: bool,
    by_merchant: bool,
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

    if scores.is_empty() {
        println!("(no usable <stem>.jpg + expected.json{} pairs in {})", if cached { " + ocr.json" } else { "" }, dir.display());
        return;
    }
    print_summary(if cached { "cached" } else { "live" }, &scores);
    if by_merchant {
        print_by_merchant(&scores);
    }
}

/// Per-merchant breakdown: groups fixtures by canonical merchant and reports the
/// same readouts as [`print_summary`]. Surfaces which merchants already reach
/// 100% (easy) vs which dense layouts drag item recall down.
fn print_by_merchant(scores: &[FixtureScore]) {
    use std::collections::BTreeMap;
    #[derive(Default)]
    struct Agg {
        n: usize,
        items_ok: usize,
        items_total: usize,
        header: usize,
        good: usize,
        full: usize,
        recall: f64,
    }
    let mut groups: BTreeMap<String, Agg> = BTreeMap::new();
    for s in scores {
        let a = groups.entry(s.merchant_group.clone()).or_default();
        let frac = if s.items_total == 0 { 1.0 } else { s.items_ok as f64 / s.items_total as f64 };
        let header = s.merchant_ok && s.date_ok && s.total_ok;
        a.n += 1;
        a.items_ok += s.items_ok;
        a.items_total += s.items_total;
        a.recall += frac;
        if header {
            a.header += 1;
        }
        if header && frac >= 0.8 - 1e-9 {
            a.good += 1;
        }
        if s.is_fully_ok() {
            a.full += 1;
        }
    }
    // Hardest last: sort by fully-fraction descending, then by size.
    let mut rows: Vec<(String, Agg)> = groups.into_iter().collect();
    rows.sort_by(|a, b| {
        let fa = a.1.full as f64 / a.1.n as f64;
        let fb = b.1.full as f64 / b.1.n as f64;
        fb.partial_cmp(&fa).unwrap().then(b.1.n.cmp(&a.1.n))
    });

    println!("\n=== by merchant (sorted by fully-correct rate) ===");
    println!("{:<22}{:>3}{:>10}{:>7}{:>8}{:>7}{:>9}", "merchant", "n", "items", "hdrOK", "good80", "FULL", "recall");
    for (m, a) in &rows {
        println!(
            "{:<22}{:>3}{:>10}{:>7}{:>8}{:>7}{:>8.0}%",
            trunc(m, 21),
            a.n,
            format!("{}/{}", a.items_ok, a.items_total),
            a.header,
            a.good,
            a.full,
            100.0 * a.recall / a.n as f64
        );
    }
}

/// Canonical merchant key for the private corpus (collapses spelling variants like
/// `NOFRILLS`/`NO FRILLS`, `FRESH`/`Bestco Fresh Foodmart`, the `FOODY MART…`
/// locations). Unknown merchants pass through unchanged.
fn merchant_group_key(m: &str) -> String {
    let u = m.to_uppercase();
    let g = if u.contains("COSTCO") {
        "Costco"
    } else if u.contains("FOODY") {
        "Foody Mart"
    } else if u.contains("FRESHCO") {
        "FreshCo"
    } else if u.contains("BESTCO") || u.trim() == "FRESH" {
        "Bestco Fresh"
    } else if u.contains("FRILL") {
        "No Frills"
    } else if u.contains("T&T") {
        "T&T"
    } else if u.contains("C&C") {
        "C&C"
    } else if u.contains("JIN LIAN") {
        "Jin Lian"
    } else if u.contains("LOBLAW") {
        "Loblaw"
    } else if u.contains("LCBO") {
        "LCBO"
    } else if u.contains("WALMART") {
        "Walmart"
    } else if u.contains("SHOPPERS") {
        "Shoppers"
    } else if u.contains("SUNNY") {
        "Sunny Foodmart"
    } else if u.contains("PREMIUM") {
        "Al-Premium"
    } else if u.contains("REAL CANADIAN") || u.contains("RCSS") {
        "Real Canadian"
    } else {
        return if m.is_empty() { "(unknown)".to_string() } else { m.to_string() };
    };
    g.to_string()
}

/// Rec-isolation: feed the DESKTOP-detected boxes (cached `.ocr.json`) through
/// OUR recognizer, then parse + score. Splits the live<cached gap into
/// detection-geometry vs recognition: result ≈ cached -> box geometry is the
/// whole story (lever = crops/unclip/resolution); result ≈ live -> our rec model
/// is the bottleneck.
fn run_reccached(
    engine: &mut OcrEngine,
    mapping: &HashMap<String, String>,
    today: (i32, u32, u32),
    dir: &Path,
) {
    let mut jpgs: Vec<PathBuf> = std::fs::read_dir(dir)
        .expect("read dir")
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            p.extension().is_some_and(|x| x == "jpg")
                && p.with_extension("ocr.json").exists()
                && p.with_extension("expected.json").exists()
        })
        .collect();
    jpgs.sort();

    let mut scores = Vec::new();
    for jpg in &jpgs {
        let name = jpg.file_stem().and_then(|s| s.to_str()).unwrap_or("image");
        let img = image::open(jpg).expect("decode image").to_rgb8();
        let padded = resize_and_pad(&img);

        // Cached desktop boxes -> quads (already in padded space, TL,TR,BR,BL).
        let v: Value = serde_json::from_str(&std::fs::read_to_string(jpg.with_extension("ocr.json")).unwrap()).unwrap();
        let (w, h) = (v["image_width"].as_i64().unwrap(), v["image_height"].as_i64().unwrap());
        let mut quads = Vec::new();
        for det in v["detections"].as_array().unwrap() {
            let Some(bbox) = det[0].as_array() else { continue };
            if bbox.len() < 4 {
                continue;
            }
            let mut pts = [[0f32; 2]; 4];
            for (i, slot) in pts.iter_mut().enumerate() {
                let p = bbox[i].as_array().unwrap();
                *slot = [p[0].as_f64().unwrap() as f32, p[1].as_f64().unwrap() as f32];
            }
            quads.push(ocr_paddle::db_postprocess::Quad { points: pts });
        }

        // OUR recognizer (+ orientation cls) on the desktop boxes.
        let dets = engine.recognize_quads(&padded, quads).expect("recognize_quads");
        let raw: Vec<RawDetection> = dets
            .into_iter()
            .map(|d| RawDetection {
                points: d.points.iter().map(|p| (p[0] as f64, p[1] as f64)).collect(),
                text: d.text,
                confidence: d.confidence as f64,
            })
            .collect();

        let pr = process_receipt(raw, w, h, OCR_IMAGE_PADDING, &format!("{name}.jpg"), None, today, "Liabilities:CreditCard", None);
        let expected: Value = serde_json::from_str(&std::fs::read_to_string(jpg.with_extension("expected.json")).unwrap()).unwrap();
        let s = score(name, &expected, &pr.parsed, mapping);
        let mark = if s.is_fully_ok() { "✓" } else { "✗" };
        println!(
            "{mark} {:<42} {:<22} items {}/{}{}",
            s.name, format!("{} / {} / {}", trunc(&s.merchant, 14), s.date, s.total_got), s.items_ok, s.items_total, s.notes()
        );
        scores.push(s);
    }
    print_summary("reccached: desktop boxes + our rec", &scores);
}

/// Print the merchant/date/total/items/fully aggregate over a set of scores.
fn print_summary(label: &str, scores: &[FixtureScore]) {
    let n = scores.len();
    if n == 0 {
        println!("(no usable fixtures)");
        return;
    }
    let merchant_ok = scores.iter().filter(|s| s.merchant_ok).count();
    let date_ok = scores.iter().filter(|s| s.date_ok).count();
    let total_ok = scores.iter().filter(|s| s.total_ok).count();
    let full_ok = scores.iter().filter(|s| s.is_fully_ok()).count();
    let items_ok: usize = scores.iter().map(|s| s.items_ok).sum();
    let items_total: usize = scores.iter().map(|s| s.items_total).sum();
    println!("\n=== device_sim summary ({label}): {n} fixtures ===");
    println!("  merchant : {merchant_ok}/{n}  ({:.0}%)", pct(merchant_ok, n));
    println!("  date     : {date_ok}/{n}  ({:.0}%)", pct(date_ok, n));
    println!("  total    : {total_ok}/{n}  ({:.0}%)", pct(total_ok, n));
    println!("  crit-items: {items_ok}/{items_total}  ({:.0}%)", pct(items_ok, items_total));
    println!("  fully OK : {full_ok}/{n}  ({:.0}%)", pct(full_ok, n));

    // Per-receipt item completeness (1.0 when a receipt has no critical items).
    let frac = |s: &FixtureScore| if s.items_total == 0 { 1.0 } else { s.items_ok as f64 / s.items_total as f64 };
    // "Header fields" = merchant + date + total all correct (the matching keys).
    let header_ok = |s: &FixtureScore| s.merchant_ok && s.date_ok && s.total_ok;
    let mean_recall = scores.iter().map(frac).sum::<f64>() / n as f64;
    let header = scores.iter().filter(|s| header_ok(s)).count();
    // "Good enough" = header fields correct AND >= T of the items captured.
    let good = |t: f64| scores.iter().filter(|s| header_ok(s) && frac(s) >= t - 1e-9).count();
    // Of the receipts that aren't fully-OK, why?
    let fail_items_only = scores.iter().filter(|s| header_ok(s) && !s.is_fully_ok()).count();
    let fail_header = n - header;

    println!("\n  --- usefulness breakdown ({n} receipts) ---");
    println!("  mean per-receipt item recall : {:.0}%", mean_recall * 100.0);
    println!("  merchant+date+total all OK    : {header}/{n}  ({:.0}%)", pct(header, n));
    println!("  good enough (m/d/t + items≥80%): {}/{n}  ({:.0}%)", good(0.80), pct(good(0.80), n));
    println!("  good enough (m/d/t + items≥90%): {}/{n}  ({:.0}%)", good(0.90), pct(good(0.90), n));
    println!("  fully OK    (m/d/t + items=100%): {full_ok}/{n}  ({:.0}%)", pct(full_ok, n));
    println!("  not-full breakdown: {fail_items_only} miss only some items (header OK) | {fail_header} miss merchant/date/total");
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
    merchant_group: String,
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

    // Group by the verified merchant when present, else the parsed one.
    let raw_merchant = expected
        .get("merchant")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or(d.merchant.as_str());

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
        merchant_group: merchant_group_key(raw_merchant),
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
