//! Shared ORT session construction.
//!
//! When built with the `coreml` feature (Apple targets), the CoreML execution
//! provider is registered so detection/recognition/classification can run on the
//! **Apple Neural Engine / GPU** instead of the CPU. EP registration is
//! best-effort: if CoreML can't take a node (unsupported op / dynamic shape) it
//! transparently falls back to the ORT CPU provider, so behaviour is unchanged
//! where acceleration isn't available.
//!
//! Runtime tuning (no rebuild needed; only meaningful with the `coreml` feature):
//! - `OCR_COREML=0` — disable CoreML, force CPU (for A/B latency comparisons).
//! - `OCR_COREML_UNITS=ane|gpu|cpu|all` — compute units (default `ane` =
//!   CPUAndNeuralEngine).
//! - `OCR_COREML_FORMAT=neuralnetwork|mlprogram` — CoreML model format (default
//!   `neuralnetwork`). MLProgram supports more ops and can be faster, but it
//!   rejects the PP-OCRv5 **server** det model (`MaxPool` `ceil_mode=True` with
//!   SAME padding), so NeuralNetwork is the safe default.
//! - `OCR_COREML_CACHE_DIR=<dir>` — cache the compiled CoreML model across
//!   launches (otherwise it recompiles on every session load — slow startup).

use std::path::Path;

use ort::session::Session;

/// Build a `Session` from a model file, applying the CoreML EP when enabled.
pub(crate) fn commit_from_file<P: AsRef<Path>>(path: P) -> ort::Result<Session> {
    #[allow(unused_mut)]
    let mut builder = Session::builder()?;

    #[cfg(feature = "coreml")]
    {
        if std::env::var("OCR_COREML").ok().as_deref() != Some("0") {
            use ort::ep::coreml::{ComputeUnits, CoreML, ModelFormat};
            let units = match std::env::var("OCR_COREML_UNITS").ok().as_deref() {
                Some("gpu") => ComputeUnits::CPUAndGPU,
                Some("cpu") => ComputeUnits::CPUOnly,
                Some("all") => ComputeUnits::All,
                _ => ComputeUnits::CPUAndNeuralEngine,
            };
            // Default NeuralNetwork: MLProgram rejects PP-OCRv5 server det
            // (MaxPool ceil_mode). Opt into MLProgram via OCR_COREML_FORMAT.
            let format = match std::env::var("OCR_COREML_FORMAT").ok().as_deref() {
                Some("mlprogram") => ModelFormat::MLProgram,
                _ => ModelFormat::NeuralNetwork,
            };
            let mut ep = CoreML::default()
                .with_compute_units(units)
                .with_model_format(format);
            if let Ok(dir) = std::env::var("OCR_COREML_CACHE_DIR") {
                ep = ep.with_model_cache_dir(dir);
            }
            // `?` converts Error<SessionBuilder> -> ort::Error; a hard failure
            // here is surfaced, but an EP that simply can't take nodes is a
            // non-fatal warning and leaves CPU execution intact.
            builder = builder.with_execution_providers([ep.build()])?;
        }
    }

    builder.commit_from_file(path)
}
