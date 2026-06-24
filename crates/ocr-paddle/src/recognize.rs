//! PP-OCRv5 text recognition via ONNX Runtime: cropped box -> (text, confidence).
//!
//! Faithful to `PP-OCRv5_mobile_rec_infer/inference.yml`: rotate-crop the quad,
//! resize to height 48 (dynamic width), normalize `(x/255 - 0.5)/0.5` in BGR
//! channel order, run the rec model, then CTC greedy-decode over the bundled
//! 18,383-char PP-OCRv5 dictionary (`CTCLabelDecode`).

use std::path::Path;

use image::{Rgb, RgbImage};
use imageproc::geometric_transformations::{warp_into, Interpolation, Projection};
use ort::session::Session;
use ort::value::Tensor;

use crate::db_postprocess::Quad;

const REC_HEIGHT: u32 = 48;
/// Bundled recognition dictionary (one character per line), extracted from the
/// model's `inference.yml`. Index 0 of the model is the CTC blank, so model class
/// `i` (>=1) maps to `DICT[i-1]`.
///
/// We ship the **English** PP-OCRv5 mobile dict (436 Latin/digit/symbol classes),
/// matching the desktop `beanbeaver-ocr` service, which runs `lang="en"` ->
/// `en_PP-OCRv5_mobile_rec`. The target receipts are bilingual (CJK + English);
/// the desktop pipeline ignores the CJK column and reads the English/numeric
/// text, and every test fixture is scored on that. A small Latin-only CTC head is
/// far less prone to digit/punctuation confusion than the 18,383-class
/// multilingual model (the on-device gap was traced to exactly such garbles), and
/// it is smaller + faster. The multilingual dict remains in `assets/` for a
/// future opt-in CJK build.
const DICT_TEXT: &str = include_str!("../assets/en_ppocrv5_rec_dict.txt");

pub struct Recognizer {
    session: Session,
    dict: Vec<String>,
}

impl Recognizer {
    pub fn from_path<P: AsRef<Path>>(path: P) -> ort::Result<Self> {
        let session = Session::builder()?.commit_from_file(path)?;
        let dict = DICT_TEXT.lines().map(|s| s.to_string()).collect();
        Ok(Self { session, dict })
    }

    /// Recognize the text inside one detected quad. Returns `(text, confidence)`.
    pub fn recognize(&mut self, img: &RgbImage, quad: &Quad) -> ort::Result<(String, f32)> {
        let crop = rotate_crop(img, quad);
        self.recognize_crop(&crop)
    }

    /// Recognize an already-cropped (and orientation-corrected) line image.
    pub fn recognize_crop(&mut self, crop: &RgbImage) -> ort::Result<(String, f32)> {
        let (data, w) = rec_preprocess(crop);

        let tensor = Tensor::from_array(([1_usize, 3, REC_HEIGHT as usize, w], data))?;
        // Copy the output out so the session borrow ends before we read `self`.
        let (logits, t, c) = {
            let outputs = self.session.run(ort::inputs![tensor])?;
            let (shape, out) = outputs[0].try_extract_tensor::<f32>()?;
            // Recognition output is (1, T, C).
            let t = shape[shape.len() - 2] as usize;
            let c = shape[shape.len() - 1] as usize;
            (out.to_vec(), t, c)
        };
        Ok(self.ctc_decode(&logits, t, c))
    }

    /// Greedy CTC decode: argmax per timestep, collapse repeats, drop blank(0).
    fn ctc_decode(&self, logits: &[f32], t: usize, c: usize) -> (String, f32) {
        let mut text = String::new();
        let mut conf_sum = 0.0f32;
        let mut conf_n = 0u32;
        let mut prev = usize::MAX;
        for ti in 0..t {
            let row = &logits[ti * c..(ti + 1) * c];
            let (idx, &p) = row
                .iter()
                .enumerate()
                .max_by(|a, b| a.1.total_cmp(b.1))
                .unwrap();
            if idx != 0 && idx != prev {
                text.push_str(&self.class_char(idx, c));
                conf_sum += p;
                conf_n += 1;
            }
            prev = idx;
        }
        let conf = if conf_n == 0 { 0.0 } else { conf_sum / conf_n as f32 };
        (text, conf)
    }

    /// Map a model class index to its character. Class 0 is blank (handled by
    /// the caller). Classes `1..=dict.len()` map to the dictionary; a trailing
    /// extra class (when `num_classes == dict.len()+2`) is the space char.
    fn class_char(&self, idx: usize, num_classes: usize) -> String {
        if idx >= 1 && idx <= self.dict.len() {
            self.dict[idx - 1].clone()
        } else if idx == self.dict.len() + 1 && num_classes == self.dict.len() + 2 {
            " ".to_string()
        } else {
            String::new()
        }
    }
}

/// Perspective-crop the quad into an upright rectangle (PaddleOCR
/// `get_rotate_crop_image`); rotate 90° when the region is tall (vertical text).
pub(crate) fn rotate_crop(img: &RgbImage, quad: &Quad) -> RgbImage {
    let p = quad.points;
    let dist = |a: [f32; 2], b: [f32; 2]| ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt();
    let crop_w = dist(p[0], p[1]).max(dist(p[2], p[3])).round().max(1.0) as u32;
    let crop_h = dist(p[0], p[3]).max(dist(p[1], p[2])).round().max(1.0) as u32;

    let dst = [
        (0.0, 0.0),
        (crop_w as f32, 0.0),
        (crop_w as f32, crop_h as f32),
        (0.0, crop_h as f32),
    ];
    let src = [
        (p[0][0], p[0][1]),
        (p[1][0], p[1][1]),
        (p[2][0], p[2][1]),
        (p[3][0], p[3][1]),
    ];

    let mut out = RgbImage::new(crop_w, crop_h);
    // warp_into samples by inverting the projection, so pass src(input)->dst(output).
    if let Some(proj) = Projection::from_control_points(src, dst) {
        warp_into(img, &proj, Interpolation::Bilinear, Rgb([0, 0, 0]), &mut out);
    }

    if crop_h as f32 / crop_w as f32 >= 1.5 {
        image::imageops::rotate270(&out)
    } else {
        out
    }
}

/// Resize crop to height 48 (dynamic width), normalize `(x/255-0.5)/0.5` in BGR,
/// CHW. Returns `(tensor_data, width)`.
fn rec_preprocess(crop: &RgbImage) -> (Vec<f32>, usize) {
    let ratio = crop.width() as f32 / crop.height() as f32;
    let w = (REC_HEIGHT as f32 * ratio).round().max(1.0) as u32;
    let resized = image::imageops::resize(crop, w, REC_HEIGHT, image::imageops::FilterType::Triangle);

    let (wu, hu) = (w as usize, REC_HEIGHT as usize);
    let plane = wu * hu;
    let mut data = vec![0f32; 3 * plane];
    for y in 0..hu {
        for x in 0..wu {
            let px = resized.get_pixel(x as u32, y as u32);
            let bgr = [px[2] as f32, px[1] as f32, px[0] as f32];
            let idx = y * wu + x;
            for ch in 0..3 {
                data[ch * plane + idx] = (bgr[ch] / 255.0 - 0.5) / 0.5;
            }
        }
    }
    (data, wu)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dict_loads_with_expected_size() {
        let r = DICT_TEXT.lines().count();
        assert_eq!(r, 436, "en_PP-OCRv5_mobile_rec dict size");
    }

    // Full det+rec on a real receipt. Run with:
    //   cargo test -p ocr-paddle -- --ignored --nocapture
    #[test]
    #[ignore = "needs converted models + fixture"]
    fn recognizes_text_on_costco_fixture() {
        use crate::detect::Detector;

        let img = image::open("../../tests/receipts_e2e/costco_20260218_redact.jpg")
            .expect("load fixture")
            .to_rgb8();
        let mut det = Detector::from_path("../../models/PP-OCRv5_mobile_det.onnx").unwrap();
        let mut rec = Recognizer::from_path("../../models/PP-OCRv5_mobile_rec.onnx").unwrap();

        let quads = det.detect(&img).unwrap();
        let mut lines: Vec<(f32, String, f32)> = quads
            .iter()
            .map(|q| {
                let cy = q.points.iter().map(|p| p[1]).sum::<f32>() / 4.0;
                let (text, conf) = rec.recognize(&img, q).unwrap();
                (cy, text, conf)
            })
            .collect();
        lines.sort_by(|a, b| a.0.total_cmp(&b.0));

        eprintln!("--- recognized {} lines (top to bottom) ---", lines.len());
        for (cy, text, conf) in &lines {
            eprintln!("y={cy:>5.0} conf={conf:.2}  {text}");
        }

        let all = lines
            .iter()
            .map(|l| l.1.to_uppercase())
            .collect::<Vec<_>>()
            .join(" ");
        assert!(
            all.contains("COSTCO") || all.contains("WHOLESALE") || all.contains("TOTAL"),
            "expected recognizable receipt tokens"
        );
    }
}
