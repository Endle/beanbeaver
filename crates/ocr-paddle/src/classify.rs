//! PP-LCNet textline-orientation classifier (the desktop's
//! `use_textline_orientation=True`): decides whether a cropped line is upside
//! down (180°) so it can be rotated before recognition.
//!
//! Config from `PP-LCNet_x1_0_textline_ori_infer/inference.yml`:
//! ResizeImage size [160, 80] (WxH), NormalizeImage ImageNet mean/std scale
//! 1/255, ToCHWImage; Topk over labels [0_degree, 180_degree].

use std::path::Path;

use image::RgbImage;
use ort::session::Session;
use ort::value::Tensor;

const CLS_W: u32 = 160;
const CLS_H: u32 = 80;
const MEAN_BGR: [f32; 3] = [0.485, 0.456, 0.406];
const STD_BGR: [f32; 3] = [0.229, 0.224, 0.225];
/// PaddleOCR's default orientation-confidence gate.
const CLS_THRESH: f32 = 0.9;

pub struct Classifier {
    session: Session,
    thresh: f32,
}

impl Classifier {
    pub fn from_path<P: AsRef<Path>>(path: P) -> ort::Result<Self> {
        let session = Session::builder()?.commit_from_file(path)?;
        Ok(Self {
            session,
            thresh: CLS_THRESH,
        })
    }

    /// Returns true when the crop is classified 180° with confidence above the
    /// gate (i.e. it should be rotated 180° before recognition).
    pub fn is_flipped(&mut self, crop: &RgbImage) -> ort::Result<bool> {
        let resized = image::imageops::resize(crop, CLS_W, CLS_H, image::imageops::FilterType::Triangle);
        let plane = (CLS_W * CLS_H) as usize;
        let mut data = vec![0f32; 3 * plane];
        for y in 0..CLS_H as usize {
            for x in 0..CLS_W as usize {
                let px = resized.get_pixel(x as u32, y as u32);
                let bgr = [px[2] as f32, px[1] as f32, px[0] as f32];
                let idx = y * CLS_W as usize + x;
                for c in 0..3 {
                    data[c * plane + idx] = (bgr[c] / 255.0 - MEAN_BGR[c]) / STD_BGR[c];
                }
            }
        }

        let tensor = Tensor::from_array(([1_usize, 3, CLS_H as usize, CLS_W as usize], data))?;
        let probs = {
            let outputs = self.session.run(ort::inputs![tensor])?;
            let (_shape, out) = outputs[0].try_extract_tensor::<f32>()?;
            out.to_vec()
        };
        // Output is [p(0°), p(180°)].
        Ok(probs.len() == 2 && probs[1] > probs[0] && probs[1] >= self.thresh)
    }
}
