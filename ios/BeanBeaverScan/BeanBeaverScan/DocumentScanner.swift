import SwiftUI
import VisionKit

/// SwiftUI wrapper around VisionKit's document scanner — auto edge-detect,
/// perspective deskew, crop, and rotate. Hands the last scanned page back as
/// JPEG bytes. Not available on the simulator (`isSupported == false`).
struct DocumentScanner: UIViewControllerRepresentable {
    var onScan: (Data) -> Void
    var onFinish: () -> Void

    func makeUIViewController(context: Context) -> VNDocumentCameraViewController {
        let controller = VNDocumentCameraViewController()
        controller.delegate = context.coordinator
        return controller
    }

    func updateUIViewController(_ controller: VNDocumentCameraViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, VNDocumentCameraViewControllerDelegate {
        private let parent: DocumentScanner
        init(_ parent: DocumentScanner) { self.parent = parent }

        func documentCameraViewController(
            _ controller: VNDocumentCameraViewController,
            didFinishWith scan: VNDocumentCameraScan
        ) {
            // Use the most recent page; encode to JPEG for the Rust seam.
            if scan.pageCount > 0,
               let data = scan.imageOfPage(at: scan.pageCount - 1).jpegData(compressionQuality: 0.9) {
                parent.onScan(data)
            }
            parent.onFinish()
        }

        func documentCameraViewControllerDidCancel(_ controller: VNDocumentCameraViewController) {
            parent.onFinish()
        }

        func documentCameraViewController(
            _ controller: VNDocumentCameraViewController,
            didFailWithError error: Error
        ) {
            parent.onFinish()
        }
    }
}
