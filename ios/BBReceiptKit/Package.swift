// swift-tools-version:5.9
import PackageDescription

// Local package wrapping the Rust receipt core. The xcframework and the
// generated Swift glue under Sources/BBReceiptKit/Generated/ are produced by
// `crates/ffi/build-xcframework.sh` (git-ignored); run it before building.
let package = Package(
    name: "BBReceiptKit",
    platforms: [.iOS(.v17)],
    products: [
        .library(name: "BBReceiptKit", targets: ["BBReceiptKit"]),
    ],
    targets: [
        .binaryTarget(
            name: "BBReceiptFFI",
            path: "Frameworks/BBReceiptFFI.xcframework"
        ),
        .target(
            name: "BBReceiptKit",
            dependencies: ["BBReceiptFFI"],
            // ONNX Runtime (inside the xcframework) needs the C++ runtime.
            linkerSettings: [.linkedLibrary("c++")]
        ),
    ]
)
