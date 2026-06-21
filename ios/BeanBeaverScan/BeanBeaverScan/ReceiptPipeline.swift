import Foundation
import Observation
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

    /// Default credit-card account for the placeholder posting; tweak in UI later.
    var creditCardAccount = "Liabilities:CreditCard"

    private var session: OcrSession?

    private func loadedSession() throws -> OcrSession {
        if let session { return session }
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
        let account = creditCardAccount
        do {
            let session = try loadedSession()
            // OCR is CPU-heavy; keep it off the main actor.
            let result = try await Task.detached(priority: .userInitiated) {
                try session.scan(imageData: imageData, creditCardAccount: account)
            }.value
            status = .done(result)
        } catch {
            status = .failed(String(describing: error))
        }
    }
}
