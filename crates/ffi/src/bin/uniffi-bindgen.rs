//! Entry point for `uniffi-bindgen` (library mode). Run e.g.:
//!   cargo run --bin uniffi-bindgen -- generate --library \
//!     target/debug/libbb_receipt_ffi.dylib --language swift --out-dir <dir>
fn main() {
    uniffi::uniffi_bindgen_main()
}
