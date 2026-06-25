import Foundation
import Observation
import os
import BBReceiptKit

/// Drives the on-device scan: load models once, then run `OcrSession.scan`
/// off the main thread and publish the result for SwiftUI.
@Observable
@MainActor
final class ReceiptPipeline {
    enum Status {
        case idle
        case scanning
        case done(ReceiptResult)
        case failed(String)
    }

    private(set) var status: Status = .idle

    /// The exact JPEG bytes (written to a temp file) that the OCR last saw, for
    /// diagnostics: export it and A/B against the desktop server to isolate the
    /// capture/encode path from the OCR model.
    private(set) var capturedImageURL: URL?

    /// Swift-observed wall time (ms) of the last `OcrSession.scan` call —
    /// includes image decode + FFI marshalling, so it's ≥ the Rust `total_ms`
    /// in `ReceiptResult.timings`. The user-perceived scan latency.
    private(set) var lastWallMs: Double?

    /// When false, forces the CPU execution provider (`OCR_COREML=0`) for an
    /// on-device CoreML/ANE-vs-CPU A/B. Changing it drops the loaded session so
    /// the next scan rebuilds the ONNX sessions with the chosen provider (the EP
    /// is selected at session-construction time from the env var).
    var coreMLEnabled = true {
        didSet {
            guard coreMLEnabled != oldValue else { return }
            session = nil
        }
    }

    /// Default credit-card account for the placeholder posting; tweak in UI later.
    var creditCardAccount = "Liabilities:CreditCard"

    private var session: OcrSession?

    /// Instruments signpost: a "scan" interval per `OcrSession.scan`, so the
    /// on-device latency shows up in the Time Profiler / os_signpost track.
    private static let signposter = OSSignposter(
        subsystem: "com.beanbeaver.BeanBeaverScan", category: "scan")

    private func loadedSession() throws -> OcrSession {
        if let session { return session }
        // Pick the execution provider before constructing the session (read once
        // by the Rust side at build time). No-op on builds without the coreml
        // feature; harmless to set regardless.
        setenv("OCR_COREML", coreMLEnabled ? "1" : "0", 1)
        guard let dir = Bundle.main.resourceURL else {
            throw NSError(domain: "BeanBeaverScan", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "No app resource bundle"])
        }
        let s = try OcrSession.load(modelsDirectory: dir)
        session = s
        return s
    }

#if DEBUG
    /// Run the pipeline on a JPEG bundled in the app (debug/demo path that
    /// bypasses the photo picker).
    func scanBundledSample(named name: String) async {
        guard let url = Bundle.main.url(forResource: name, withExtension: "jpg"),
              let data = try? Data(contentsOf: url) else {
            status = .failed("Bundled sample \(name).jpg not found")
            return
        }
        await scan(imageData: data)
    }
#endif

    func scan(imageData: Data) async {
        status = .scanning
        capturedImageURL = persistCapture(imageData)
        lastWallMs = nil
        let account = creditCardAccount
        do {
            let session = try loadedSession()
            let signpost = Self.signposter.beginInterval("scan")
            let started = Date()
            // OCR is CPU-heavy; keep it off the main actor.
            let result = try await Task.detached(priority: .userInitiated) {
                try session.scan(imageData: imageData, creditCardAccount: account)
            }.value
            lastWallMs = Date().timeIntervalSince(started) * 1000
            Self.signposter.endInterval("scan", signpost)
            status = .done(result)
        } catch {
            status = .failed(String(describing: error))
        }
    }

    /// Write the captured JPEG to a timestamped temp file so it can be exported
    /// via the share sheet (AirDrop / Files / Mail).
    private func persistCapture(_ data: Data) -> URL? {
        let stamp = Int(Date().timeIntervalSince1970)
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("receipt_capture_\(stamp).jpg")
        do {
            try data.write(to: url)
            return url
        } catch {
            return nil
        }
    }
}

#if DEBUG
extension ReceiptPipeline {
    /// Preview/screenshot-only factory pinned to a given status, so SwiftUI
    /// previews can render every UI state without running OCR. Must live in this
    /// file: `status`'s setter is `private`.
    static func preview(_ status: Status) -> ReceiptPipeline {
        let pipeline = ReceiptPipeline()
        pipeline.status = status
        return pipeline
    }
}
#endif
