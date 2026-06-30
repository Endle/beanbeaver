//! PP-OCRv5 text detection via ONNX Runtime: image -> quad boxes.

use std::path::Path;

use image::RgbImage;
use ort::session::Session;
use ort::value::Tensor;

use crate::db_postprocess::{boxes_from_bitmap, DbConfig, Quad};
use crate::preprocess::{preprocess_det, preprocess_det_fixed};

pub struct Detector {
    session: Session,
    cfg: DbConfig,
    /// `Some((H, W))` when the model has a static input shape (e.g. the
    /// CoreML/ANE server det export) — then detection letterboxes to it.
    fixed: Option<(usize, usize)>,
}

/// Read a static `(H, W)` from the model's first input, or `None` if dynamic.
fn static_input_hw(session: &Session) -> Option<(usize, usize)> {
    let inp = session.inputs().first()?;
    if let ort::value::ValueType::Tensor { shape, .. } = inp.dtype() {
        let r = shape.len();
        if r >= 4 && shape[r - 2] > 0 && shape[r - 1] > 0 {
            return Some((shape[r - 2] as usize, shape[r - 1] as usize));
        }
    }
    None
}

/// Raw DB probability map plus the geometry needed to post-process it. Exposed
/// for diagnostics (`device_sim --probdump`): lets the same prob map be fed
/// through both our `boxes_from_bitmap` and PaddleOCR's reference DBPostProcess,
/// isolating the contour/min-rect/unclip algorithm from the upstream mask.
pub struct DetProb {
    pub prob: Vec<f32>,
    pub h: usize,
    pub w: usize,
    pub orig_w: f32,
    pub orig_h: f32,
    pub ratio_w: f32,
    pub ratio_h: f32,
}

impl Detector {
    pub fn from_path<P: AsRef<Path>>(path: P) -> ort::Result<Self> {
        let session = crate::session::commit_from_file(path)?;
        let fixed = static_input_hw(&session);
        Ok(Self {
            session,
            cfg: DbConfig::default(),
            fixed,
        })
    }

    /// Run detection up to (and including) the DB probability map, returning it
    /// raw — without the contour/box post-processing. Diagnostics only.
    pub fn prob_map(&mut self, img: &RgbImage) -> ort::Result<DetProb> {
        let input = preprocess_det(img);
        let (orig_w, orig_h) = (img.width() as f32, img.height() as f32);
        let tensor = Tensor::from_array((
            [1_usize, 3, input.height, input.width],
            input.data.clone(),
        ))?;
        let outputs = self.session.run(ort::inputs![tensor])?;
        let (shape, data) = outputs[0].try_extract_tensor::<f32>()?;
        let h = shape[shape.len() - 2] as usize;
        let w = shape[shape.len() - 1] as usize;
        Ok(DetProb {
            prob: data.to_vec(),
            h,
            w,
            orig_w,
            orig_h,
            ratio_w: input.ratio_w,
            ratio_h: input.ratio_h,
        })
    }

    /// Detect text-region quads (original-image pixel coords). Uses the
    /// fixed-canvas letterbox path for static-shape models, else the variable
    /// resize-long path.
    pub fn detect(&mut self, img: &RgbImage) -> ort::Result<Vec<Quad>> {
        match self.fixed {
            Some((th, tw)) => self.detect_fixed(img, th, tw),
            None => self.detect_dynamic(img),
        }
    }

    /// Variable resize-long path (PP-OCRv5 mobile/server dynamic-shape models).
    fn detect_dynamic(&mut self, img: &RgbImage) -> ort::Result<Vec<Quad>> {
        let input = preprocess_det(img);
        let (orig_w, orig_h) = (img.width() as f32, img.height() as f32);

        let tensor = Tensor::from_array((
            [1_usize, 3, input.height, input.width],
            input.data.clone(),
        ))?;
        // Single positional input binds to the model's only input ("x").
        let outputs = self.session.run(ort::inputs![tensor])?;

        let (shape, data) = outputs[0].try_extract_tensor::<f32>()?;
        // DB output is (1, 1, H, W).
        let h = shape[shape.len() - 2] as usize;
        let w = shape[shape.len() - 1] as usize;

        Ok(boxes_from_bitmap(
            data,
            h,
            w,
            orig_w,
            orig_h,
            input.ratio_w,
            input.ratio_h,
            &self.cfg,
        ))
    }

    /// Fixed-canvas letterbox path (static-shape models). Boxes are extracted in
    /// canvas coords (ratio 1), then mapped back via `(p - pad) / scale`.
    fn detect_fixed(&mut self, img: &RgbImage, th: usize, tw: usize) -> ort::Result<Vec<Quad>> {
        let (orig_w, orig_h) = (img.width() as f32, img.height() as f32);
        let fx = preprocess_det_fixed(img, th as u32, tw as u32);

        let tensor = Tensor::from_array(([1_usize, 3, th, tw], fx.data.clone()))?;
        let outputs = self.session.run(ort::inputs![tensor])?;
        let (shape, data) = outputs[0].try_extract_tensor::<f32>()?;
        let h = shape[shape.len() - 2] as usize;
        let w = shape[shape.len() - 1] as usize;

        // ratio 1 + canvas dims => boxes (incl. unclip) in canvas pixel coords.
        let mut quads = boxes_from_bitmap(data, h, w, w as f32, h as f32, 1.0, 1.0, &self.cfg);
        for q in quads.iter_mut() {
            for p in q.points.iter_mut() {
                p[0] = ((p[0] - fx.pad_x) / fx.scale).clamp(0.0, orig_w);
                p[1] = ((p[1] - fx.pad_y) / fx.scale).clamp(0.0, orig_h);
            }
        }
        Ok(quads)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Requires the (gitignored) converted model + a fixture image; run with:
    //   cargo test -p ocr-paddle -- --ignored --nocapture
    #[test]
    #[ignore = "needs models/PP-OCRv5_mobile_det.onnx"]
    fn detects_text_boxes_on_costco_fixture() {
        let model = "../../models/PP-OCRv5_mobile_det.onnx";
        let image_path = "../../tests/receipts_e2e/costco_20260218_redact.jpg";

        let img = image::open(image_path).expect("load fixture").to_rgb8();
        let mut det = Detector::from_path(model).expect("load det model");
        let quads = det.detect(&img).expect("run detection");

        eprintln!(
            "costco {}x{}: detected {} boxes",
            img.width(),
            img.height(),
            quads.len()
        );
        for q in quads.iter().take(5) {
            eprintln!("  box: {:?}", q.points);
        }
        // A Costco receipt has dozens of text lines; sanity-check we found many.
        assert!(quads.len() > 20, "expected many boxes, got {}", quads.len());
    }
}
