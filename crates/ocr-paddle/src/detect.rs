//! PP-OCRv5 text detection via ONNX Runtime: image -> quad boxes.

use std::path::Path;

use image::RgbImage;
use ort::session::Session;
use ort::value::Tensor;

use crate::db_postprocess::{boxes_from_bitmap, DbConfig, Quad};
use crate::preprocess::preprocess_det;

pub struct Detector {
    session: Session,
    cfg: DbConfig,
}

impl Detector {
    pub fn from_path<P: AsRef<Path>>(path: P) -> ort::Result<Self> {
        let session = Session::builder()?.commit_from_file(path)?;
        Ok(Self {
            session,
            cfg: DbConfig::default(),
        })
    }

    /// Detect text-region quads (original-image pixel coords).
    pub fn detect(&mut self, img: &RgbImage) -> ort::Result<Vec<Quad>> {
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
