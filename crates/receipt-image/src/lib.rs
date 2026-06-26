//! Pre-OCR image preprocessing, shared by the desktop app (via the `_rust_matcher`
//! PyO3 extension) and iOS (`ocr-paddle`).
//!
//! This is a faithful Rust port of `receipt/image_pipeline.py`'s
//! `default_image_pipeline` + the encode in `resize_image_bytes`:
//!
//! ```text
//! decode -> EXIF transpose -> Lanczos resize (cap long side) -> white pad -> JPEG
//! ```
//!
//! Deskew is deliberately NOT included: the Python default already excludes it
//! (it regressed 4/39 real receipts and helped zero — see image_pipeline.py).
//!
//! NOTE on byte-parity: the desktop output is fed to the external PaddleOCR
//! container, so this must stay *close* to Pillow. The two places it can drift
//! are the Lanczos kernel and the JPEG encoder (Pillow vs the `image` crate);
//! gate any rollout on a detection-baseline check.

use std::io::Cursor;

use image::codecs::jpeg::JpegEncoder;
use image::imageops::FilterType;
use image::{DynamicImage, ExtendedColorType, ImageDecoder, ImageEncoder, ImageReader, Rgb, RgbImage};

/// Matches `image_pipeline.MAX_IMAGE_DIMENSION`.
pub const MAX_IMAGE_DIMENSION: u32 = 3000;
/// Matches `image_pipeline.OCR_IMAGE_PADDING`.
pub const OCR_IMAGE_PADDING: u32 = 50;
/// Matches the `quality=95` in `resize_image_bytes`.
pub const JPEG_QUALITY: u8 = 95;

/// Failure decoding the input or encoding the output.
#[derive(Debug)]
pub enum PreprocessError {
    Decode(String),
    Encode(String),
}

impl std::fmt::Display for PreprocessError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PreprocessError::Decode(m) => write!(f, "image decode failed: {m}"),
            PreprocessError::Encode(m) => write!(f, "jpeg encode failed: {m}"),
        }
    }
}

impl std::error::Error for PreprocessError {}

/// Full pre-OCR pipeline on encoded image bytes (JPEG/PNG in → JPEG out), the
/// drop-in replacement for Python `resize_image_bytes`.
pub fn preprocess_image_bytes(
    bytes: &[u8],
    max_dim: u32,
    padding: u32,
    quality: u8,
) -> Result<Vec<u8>, PreprocessError> {
    let rgb = decode_oriented_rgb(bytes)?;
    let resized = resize_cap_long_side(&rgb, max_dim);
    let padded = pad_white(&resized, padding);
    encode_jpeg(&padded, quality)
}

/// Decode + apply EXIF orientation, returning a canonical RGB frame — mirrors
/// `ImageOps.exif_transpose` followed by `convert("RGB")`.
fn decode_oriented_rgb(bytes: &[u8]) -> Result<RgbImage, PreprocessError> {
    let reader = ImageReader::new(Cursor::new(bytes))
        .with_guessed_format()
        .map_err(|e| PreprocessError::Decode(e.to_string()))?;
    let mut decoder = reader
        .into_decoder()
        .map_err(|e| PreprocessError::Decode(e.to_string()))?;
    // EXIF orientation (no-op when absent, e.g. iOS VisionKit-oriented input).
    let orientation = decoder
        .orientation()
        .map_err(|e| PreprocessError::Decode(e.to_string()))?;
    let mut img =
        DynamicImage::from_decoder(decoder).map_err(|e| PreprocessError::Decode(e.to_string()))?;
    img.apply_orientation(orientation);
    Ok(img.to_rgb8())
}

/// Cap the longer side at `max_dim` with Lanczos, mirroring `resize_max_dim_op`.
/// Uses Python `int()` truncation for the derived side (NOT rounding) so the
/// output dimensions match Pillow exactly.
fn resize_cap_long_side(img: &RgbImage, max_dim: u32) -> RgbImage {
    let (w, h) = (img.width(), img.height());
    if w <= max_dim && h <= max_dim {
        return img.clone();
    }
    let (nw, nh) = if w > h {
        (max_dim, (h as f64 * (max_dim as f64 / w as f64)) as u32)
    } else {
        ((w as f64 * (max_dim as f64 / h as f64)) as u32, max_dim)
    };
    image::imageops::resize(img, nw.max(1), nh.max(1), FilterType::Lanczos3)
}

/// Surround with `padding` px of white, mirroring `ImageOps.expand(fill="white")`.
fn pad_white(img: &RgbImage, padding: u32) -> RgbImage {
    if padding == 0 {
        return img.clone();
    }
    let mut out = RgbImage::from_pixel(
        img.width() + 2 * padding,
        img.height() + 2 * padding,
        Rgb([255, 255, 255]),
    );
    image::imageops::overlay(&mut out, img, padding as i64, padding as i64);
    out
}

/// Encode RGB → baseline JPEG at `quality`, mirroring `save(format="JPEG", quality=…)`.
fn encode_jpeg(img: &RgbImage, quality: u8) -> Result<Vec<u8>, PreprocessError> {
    let mut buf = Vec::new();
    JpegEncoder::new_with_quality(&mut buf, quality)
        .write_image(img.as_raw(), img.width(), img.height(), ExtendedColorType::Rgb8)
        .map_err(|e| PreprocessError::Encode(e.to_string()))?;
    Ok(buf)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn jpeg_of(img: &RgbImage) -> Vec<u8> {
        encode_jpeg(img, 95).unwrap()
    }

    fn dims(jpeg: &[u8]) -> (u32, u32) {
        let img = image::load_from_memory(jpeg).unwrap().to_rgb8();
        (img.width(), img.height())
    }

    #[test]
    fn caps_long_side_and_pads() {
        // 4000x2000 landscape → cap width to 3000, height int(2000*3000/4000)=1500,
        // then +50px border each side → 3100 x 1600.
        let src = RgbImage::from_pixel(4000, 2000, Rgb([120, 130, 140]));
        let out = preprocess_image_bytes(&jpeg_of(&src), 3000, 50, 95).unwrap();
        assert_eq!(dims(&out), (3100, 1600));
    }

    #[test]
    fn small_image_only_padded() {
        let src = RgbImage::from_pixel(100, 80, Rgb([10, 20, 30]));
        let out = preprocess_image_bytes(&jpeg_of(&src), 3000, 50, 95).unwrap();
        assert_eq!(dims(&out), (200, 180));
    }

    #[test]
    fn zero_padding_is_noop_border() {
        let src = RgbImage::from_pixel(120, 90, Rgb([0, 0, 0]));
        let out = preprocess_image_bytes(&jpeg_of(&src), 3000, 0, 95).unwrap();
        assert_eq!(dims(&out), (120, 90));
    }

    #[test]
    fn truncates_like_python_int() {
        // 3001x1000 portrait-of-width: w>h, cap width 3000,
        // height int(1000 * 3000/3001) = int(999.667) = 999, +0 pad.
        let src = RgbImage::from_pixel(3001, 1000, Rgb([200, 200, 200]));
        let out = preprocess_image_bytes(&jpeg_of(&src), 3000, 0, 95).unwrap();
        assert_eq!(dims(&out), (3000, 999));
    }

    #[test]
    fn pad_corners_are_white() {
        let src = RgbImage::from_pixel(40, 40, Rgb([10, 10, 10]));
        let padded = pad_white(&src, 5);
        assert_eq!(*padded.get_pixel(0, 0), Rgb([255, 255, 255]));
        assert_eq!(*padded.get_pixel(7, 7), Rgb([10, 10, 10]));
    }
}
