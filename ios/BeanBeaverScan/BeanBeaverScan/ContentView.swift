import SwiftUI
import PhotosUI
import VisionKit
import BBReceiptKit

struct ContentView: View {
    @State private var pipeline = ReceiptPipeline()
    @State private var photoItem: PhotosPickerItem?
    @State private var showScanner = false

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

                    statusView
                }
                .padding()
            }
            .navigationTitle("BeanBeaver Scan")
            .fullScreenCover(isPresented: $showScanner) {
                DocumentScanner(
                    onScan: { data in Task { await pipeline.scan(imageData: data) } },
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
            Label(message, systemImage: "exclamationmark.triangle")
                .foregroundStyle(.red)
        case .done(let result):
            ReceiptResultView(result: result)
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
