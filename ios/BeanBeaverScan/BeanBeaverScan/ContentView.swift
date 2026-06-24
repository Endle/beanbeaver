import SwiftUI
import PhotosUI
import VisionKit
import BBReceiptKit

struct ContentView: View {
    @State private var pipeline = ReceiptPipeline()
    @State private var photoItem: PhotosPickerItem?
    @State private var showScanner = false

    /// When on, a copy of each camera-scanned receipt is saved to the camera roll.
    @AppStorage("saveScansToPhotos") private var saveScansToPhotos = false

    /// Bundled DEBUG sample (a redacted Costco receipt fixture).
    private let sampleName = "costco_20260218_redact"

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if VNDocumentCameraViewController.isSupported {
                        Button {
                            showScanner = true
                        } label: {
                            Label("Scan a receipt", systemImage: "doc.viewfinder")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                    }

                    PhotosPicker(selection: $photoItem, matching: .images) {
                        Label("Pick a receipt photo", systemImage: "photo.on.rectangle")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)

#if DEBUG
                    Button {
                        Task { await pipeline.scanBundledSample(named: sampleName) }
                    } label: {
                        Label("Run bundled sample", systemImage: "doc.text.magnifyingglass")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
#endif

                    if VNDocumentCameraViewController.isSupported {
                        Toggle("Save scans to Photos", isOn: $saveScansToPhotos)
                            .font(.subheadline)
                    }

                    statusView
                }
                .padding()
            }
            .navigationTitle("BeanBeaver Scan")
            .fullScreenCover(isPresented: $showScanner) {
                DocumentScanner(
                    onScan: { data in
                        if saveScansToPhotos { PhotoSaver.save(imageData: data) }
                        Task { await pipeline.scan(imageData: data) }
                    },
                    onFinish: { showScanner = false }
                )
                .ignoresSafeArea()
            }
#if DEBUG
            .task {
                // Lets `simctl launch … -autoRunSample` exercise the pipeline
                // headlessly for screenshots/verification.
                if ProcessInfo.processInfo.arguments.contains("-autoRunSample") {
                    await pipeline.scanBundledSample(named: sampleName)
                }
            }
#endif
            .onChange(of: photoItem) { _, item in
                guard let item else { return }
                Task {
                    guard let data = try? await item.loadTransferable(type: Data.self) else { return }
                    await pipeline.scan(imageData: data)
                }
            }
        }
    }

    @ViewBuilder
    private var statusView: some View {
        switch pipeline.status {
        case .idle:
            Text("Pick a receipt and it's parsed entirely on-device — OCR, parse, and beancount.")
                .foregroundStyle(.secondary)
        case .scanning:
            HStack { ProgressView(); Text("Scanning…") }
        case .failed(let message):
            VStack(alignment: .leading, spacing: 12) {
                Label(message, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.red)
                capturedImageExport
            }
        case .done(let result):
            VStack(alignment: .leading, spacing: 12) {
                ReceiptResultView(result: result)
                capturedImageExport
            }
        }
    }

    /// Export the exact JPEG the OCR saw, to A/B against the desktop server.
    @ViewBuilder
    private var capturedImageExport: some View {
        if let url = pipeline.capturedImageURL {
            ShareLink(item: url) {
                Label("Export captured image", systemImage: "photo.badge.arrow.down")
            }
        }
    }
}

struct ReceiptResultView: View {
    let result: ReceiptResult

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(result.merchant).font(.title2).bold()
                if let date = result.date {
                    Text(date).foregroundStyle(.secondary)
                }
                Text("Total \(result.total)").font(.headline)
            }

            if !result.items.isEmpty {
                Divider()
                ForEach(Array(result.items.enumerated()), id: \.offset) { _, item in
                    HStack {
                        Text(item.description).lineLimit(1)
                        Spacer()
                        if let category = item.category {
                            Text(category).font(.caption).foregroundStyle(.secondary)
                        }
                        Text(item.price).monospacedDigit()
                    }
                }
            }

            Divider()
            Text("beancount").font(.caption).foregroundStyle(.secondary)
            Text(result.beancount)
                .font(.system(.footnote, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))

            ShareLink(item: result.beancount) {
                Label("Export beancount", systemImage: "square.and.arrow.up")
            }
        }
    }
}

// MARK: - Previews

#if DEBUG
extension ContentView {
    /// Preview/screenshot-only initializer that injects a pinned-status pipeline
    /// so the whole screen renders in any state without running OCR.
    init(previewPipeline: ReceiptPipeline) {
        _pipeline = State(initialValue: previewPipeline)
    }
}

extension ReceiptResult {
    /// A rich, fully-populated result (mirrors the bundled Costco fixture).
    static let previewFull = ReceiptResult(
        merchant: "Costco Wholesale",
        date: "2026-02-18",
        dateIsPlaceholder: false,
        total: "$148.73",
        tax: "$9.42",
        subtotal: "$139.31",
        items: [
            ReceiptItem(description: "ORG BANANAS", price: "$2.49", quantity: 1, category: "Groceries"),
            ReceiptItem(description: "ROTISSERIE CHICKEN", price: "$4.99", quantity: 1, category: "Groceries"),
            ReceiptItem(description: "KIRKLAND OLIVE OIL 2L", price: "$21.99", quantity: 1, category: "Groceries"),
            ReceiptItem(description: "BATH TISSUE 30 ROLL", price: "$24.99", quantity: 1, category: "Household"),
            ReceiptItem(description: "GASOLINE REGULAR", price: "$58.40", quantity: 1, category: nil),
        ],
        warnings: [],
        beancount: """
        2026-02-18 * "Costco Wholesale"
          Expenses:Groceries          54.45 USD
          Expenses:Household          24.99 USD
          Expenses:Uncategorized      58.40 USD
          Liabilities:CreditCard     -148.73 USD
        """
    )

    /// A sparse result: no line items, inferred date, parser warnings.
    static let previewMinimal = ReceiptResult(
        merchant: "Corner Cafe",
        date: nil,
        dateIsPlaceholder: true,
        total: "$6.50",
        tax: nil,
        subtotal: nil,
        items: [],
        warnings: ["No line items detected", "Date inferred from today"],
        beancount: """
        2026-06-24 * "Corner Cafe"
          Expenses:Uncategorized       6.50 USD
          Liabilities:CreditCard      -6.50 USD
        """
    )
}

#Preview("Result – full") {
    ScrollView { ReceiptResultView(result: .previewFull).padding() }
}

#Preview("Result – minimal") {
    ScrollView { ReceiptResultView(result: .previewMinimal).padding() }
}

#Preview("Screen – idle") {
    ContentView()
}

#Preview("Screen – scanning") {
    ContentView(previewPipeline: .preview(.scanning))
}

#Preview("Screen – done") {
    ContentView(previewPipeline: .preview(.done(.previewFull)))
}

#Preview("Screen – failed") {
    ContentView(previewPipeline: .preview(.failed("Couldn't read this receipt. Try retaking the photo in better light.")))
}
#endif
