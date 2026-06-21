//! PP-OCRv5 detection preprocessing, faithful to the desktop config
//! (`PP-OCRv5_mobile_det_infer/inference.yml`):
//!
//! ```yaml
//! DetResizeForTest: { resize_long: 960 }
//! NormalizeImage: { mean: [0.485,0.456,0.406], std: [0.229,0.224,0.225],
//!                   scale: 1./255., order: hwc }   # applied in BGR channel order
//! ToCHWImage
//! ```
//!
//! DecodeImage uses `img_mode: BGR`, so the mean/std are applied to channels in
//! **B, G, R** order; the resulting NCHW tensor therefore has channel 0 = B.
//! We replicate that exactly so the ONNX input matches what the server feeds.

use image::RgbImage;

/// Longest-side target for detection resize. PaddleOCR's `DetResizeForTest`
/// default is 960, but on dense receipts (150+ small lines) 960 under-detects:
/// adjacent rows merge / thin rows vanish. Detecting at 1536 recovers them and
/// lifts end-to-end critical-item extraction 51%->61% on the private corpus
/// (vs the same parser's 95% on desktop OCR) at no model-size cost. Higher
/// values help recall further but cost detection latency.
pub const RESIZE_LONG: u32 = 1536;
/// DB models are fully convolutional but require H,W to be multiples of 32.
pub const STRIDE: u32 = 32;

/// ImageNet mean/std in the order PaddleOCR applies them to a BGR image.
const MEAN_BGR: [f32; 3] = [0.485, 0.456, 0.406];
const STD_BGR: [f32; 3] = [0.229, 0.224, 0.225];

/// Detection model input: an NCHW (N=1, C=3) float tensor plus the resize ratios
/// needed to map detected boxes back to original-image pixel coordinates.
#[derive(Clone, Debug)]
pub struct DetInput {
    pub data: Vec<f32>,
    pub height: usize,
    pub width: usize,
    /// resized_height / original_height
    pub ratio_h: f32,
    /// resized_width / original_width
    pub ratio_w: f32,
}

/// Compute the resized (height, width), both rounded up to a multiple of
/// [`STRIDE`], with the longer side scaled to [`RESIZE_LONG`]
/// (PaddleOCR `DetResizeForTest` / `resize_image_type2`).
pub fn resized_dims(orig_w: u32, orig_h: u32) -> (u32, u32) {
    let longer = orig_w.max(orig_h) as f32;
    let ratio = RESIZE_LONG as f32 / longer;
    let round_up = |v: f32| -> u32 {
        let n = (v.round() as u32).max(1);
        n.div_ceil(STRIDE) * STRIDE
    };
    let rh = round_up(orig_h as f32 * ratio);
    let rw = round_up(orig_w as f32 * ratio);
    (rw, rh)
}

/// Preprocess an RGB image into the detection model's NCHW input tensor.
pub fn preprocess_det(img: &RgbImage) -> DetInput {
    let (orig_w, orig_h) = (img.width(), img.height());
    let (rw, rh) = resized_dims(orig_w, orig_h);

    // cv2.resize default is bilinear; Triangle is the bilinear filter.
    let resized = image::imageops::resize(img, rw, rh, image::imageops::FilterType::Triangle);

    let (rw_u, rh_u) = (rw as usize, rh as usize);
    let plane = rh_u * rw_u;
    let mut data = vec![0f32; 3 * plane];

    for y in 0..rh_u {
        for x in 0..rw_u {
            let px = resized.get_pixel(x as u32, y as u32);
            // image crate yields RGB; reorder to BGR to match DecodeImage(BGR).
            let bgr = [px[2] as f32, px[1] as f32, px[0] as f32];
            let idx = y * rw_u + x;
            for c in 0..3 {
                let v = (bgr[c] / 255.0 - MEAN_BGR[c]) / STD_BGR[c];
                data[c * plane + idx] = v;
            }
        }
    }

    DetInput {
        data,
        height: rh_u,
        width: rw_u,
        ratio_h: rh as f32 / orig_h as f32,
        ratio_w: rw as f32 / orig_w as f32,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::RgbImage;

    // Expected dim: scale by ratio, round, then round up to a STRIDE multiple.
    fn expect_dim(orig: u32, ratio: f32) -> u32 {
        ((orig as f32 * ratio).round() as u32).max(1).div_ceil(STRIDE) * STRIDE
    }

    #[test]
    fn resized_dims_scale_longer_side_to_resize_long_and_pad_to_stride() {
        // 400x800: longer side (800) scaled to RESIZE_LONG; shorter by same ratio.
        let (w, h) = resized_dims(400, 800);
        let ratio = RESIZE_LONG as f32 / 800.0;
        assert_eq!(h, expect_dim(800, ratio));
        assert_eq!(w, expect_dim(400, ratio));
        assert_eq!(w % STRIDE, 0);
        assert_eq!(h % STRIDE, 0);
    }

    #[test]
    fn resized_dims_round_up_to_multiple_of_stride() {
        // Square stays square at the RESIZE_LONG (a STRIDE multiple).
        assert_eq!(resized_dims(1000, 1000), (RESIZE_LONG, RESIZE_LONG));
        // Non-divisible result rounds up to a STRIDE multiple.
        let (w, h) = resized_dims(700, 1000);
        let ratio = RESIZE_LONG as f32 / 1000.0;
        assert_eq!(h, RESIZE_LONG);
        assert_eq!(w, expect_dim(700, ratio));
        assert_eq!(w % STRIDE, 0);
    }

    #[test]
    fn preprocess_produces_nchw_tensor_with_correct_length() {
        let img = RgbImage::new(200, 100);
        let input = preprocess_det(&img);
        assert_eq!(input.data.len(), 3 * input.height * input.width);
        assert_eq!(input.width % STRIDE as usize, 0);
        assert_eq!(input.height % STRIDE as usize, 0);
        // A black image (0,0,0) normalizes to (0 - mean)/std per channel.
        let expected_b = (0.0 - MEAN_BGR[0]) / STD_BGR[0];
        assert!((input.data[0] - expected_b).abs() < 1e-6);
    }
}
