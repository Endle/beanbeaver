import Photos

/// Saves a captured receipt image to the user's photo library (camera roll).
enum PhotoSaver {
    /// Save encoded image bytes (JPEG/PNG) to Photos using add-only access.
    /// Requests permission on first use; a no-op if the user declines.
    static func save(imageData: Data) {
        PHPhotoLibrary.requestAuthorization(for: .addOnly) { status in
            guard status == .authorized || status == .limited else { return }
            PHPhotoLibrary.shared().performChanges {
                PHAssetCreationRequest.forAsset().addResource(with: .photo, data: imageData, options: nil)
            } completionHandler: { _, _ in }
        }
    }
}
