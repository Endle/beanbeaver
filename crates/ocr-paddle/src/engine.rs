//! Full on-device OCR engine: image -> detections (`[bbox, [text, conf]]`),
//! assembling detection + textline-orientation cls + recognition, mirroring the
//! desktop `beanbeaver-ocr` service output.

use std::path::Path;

use image::RgbImage;

use crate::classify::Classifier;
use crate::detect::Detector;
use crate::recognize::{rotate_crop, Recognizer};

/// PaddleOCR default: drop recognized lines below this confidence.
const DROP_SCORE: f32 = 0.5;

/// One recognized text region, in original-image pixel coordinates.
#[derive(Clone, Debug)]
pub struct Detection {
    pub points: [[f32; 2]; 4],
    pub text: String,
    pub confidence: f32,
}

pub struct OcrEngine {
    detector: Detector,
    classifier: Option<Classifier>,
    recognizer: Recognizer,
}

impl OcrEngine {
    /// Load det + rec (+ optional textline-orientation cls) models.
    pub fn from_paths<P: AsRef<Path>>(
        det_model: P,
        rec_model: P,
        cls_model: Option<P>,
    ) -> ort::Result<Self> {
        let classifier = match cls_model {
            Some(p) => Some(Classifier::from_path(p)?),
            None => None,
        };
        Ok(Self {
            detector: Detector::from_path(det_model)?,
            classifier,
            recognizer: Recognizer::from_path(rec_model)?,
        })
    }

    /// Detect + (orient) + recognize every text region in the image.
    pub fn recognize_image(&mut self, img: &RgbImage) -> ort::Result<Vec<Detection>> {
        let quads = self.detector.detect(img)?;
        self.recognize_quads(img, quads)
    }

    /// Orient + recognize a caller-supplied set of quads (skips detection).
    /// Used by the `device_sim --reccached` diagnostic to feed desktop-detected
    /// boxes through our recognizer, isolating recognition from detection.
    pub fn recognize_quads(
        &mut self,
        img: &RgbImage,
        quads: Vec<crate::db_postprocess::Quad>,
    ) -> ort::Result<Vec<Detection>> {
        // Debug probe: REC_DUMP_DIR=<dir> saves every line's pre-rec crop PNG and
        // logs box/conf/text (incl. dropped lines), to localize garbles to
        // crop-extraction vs recognition. Off unless the env var is set.
        let dump = std::env::var("REC_DUMP_DIR").ok();
        if let Some(d) = &dump {
            let _ = std::fs::create_dir_all(d);
        }
        let mut out = Vec::with_capacity(quads.len());
        for (i, q) in quads.into_iter().enumerate() {
            let mut crop = rotate_crop(img, &q);
            if let Some(cls) = self.classifier.as_mut() {
                if cls.is_flipped(&crop)? {
                    crop = image::imageops::rotate180(&crop);
                }
            }
            let (text, confidence) = self.recognizer.recognize_crop(&crop)?;
            if let Some(d) = &dump {
                let (mut x0, mut y0, mut x1, mut y1) = (f32::MAX, f32::MAX, f32::MIN, f32::MIN);
                for p in &q.points {
                    x0 = x0.min(p[0]);
                    y0 = y0.min(p[1]);
                    x1 = x1.max(p[0]);
                    y1 = y1.max(p[1]);
                }
                let safe: String = text.chars().map(|c| if c.is_alphanumeric() { c } else { '_' }).take(20).collect();
                let _ = crop.save(format!("{d}/{i:03}_c{confidence:.2}_{safe}.png"));
                eprintln!("REC {i:03} box=({x0:.0},{y0:.0},{x1:.0},{y1:.0}) conf={confidence:.2} text={text:?}");
            }
            if text.is_empty() || confidence < DROP_SCORE {
                continue;
            }
            out.push(Detection {
                points: q.points,
                text,
                confidence,
            });
        }
        Ok(out)
    }
}
