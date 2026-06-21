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
        let mut out = Vec::with_capacity(quads.len());
        for q in quads {
            let mut crop = rotate_crop(img, &q);
            if let Some(cls) = self.classifier.as_mut() {
                if cls.is_flipped(&crop)? {
                    crop = image::imageops::rotate180(&crop);
                }
            }
            let (text, confidence) = self.recognizer.recognize_crop(&crop)?;
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
