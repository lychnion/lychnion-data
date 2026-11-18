// OCR fallback for PDFs whose text layer is unreadable. macOS Vision, Japanese.
// Usage: swift tool/pdf_ocr.swift file.pdf
import Foundation
import PDFKit
import Vision

guard CommandLine.arguments.count == 2,
      let doc = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])) else {
    FileHandle.standardError.write("usage: pdf_ocr.swift file.pdf\n".data(using: .utf8)!)
    exit(1)
}

for i in 0..<doc.pageCount {
    guard let page = doc.page(at: i) else { continue }
    let box = page.bounds(for: .mediaBox)
    let scale: CGFloat = 3  // about 216 dpi
    let width = Int(box.width * scale), height = Int(box.height * scale)
    guard let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                              bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { continue }
    ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
    ctx.scaleBy(x: scale, y: scale)
    page.draw(with: .mediaBox, to: ctx)
    guard let image = ctx.makeImage() else { continue }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["ja-JP", "en-US"]
    request.usesLanguageCorrection = true
    try? VNImageRequestHandler(cgImage: image).perform([request])
    // Top to bottom, then left to right.
    let lines = (request.results ?? [])
        .sorted { a, b in
            abs(a.boundingBox.midY - b.boundingBox.midY) > 0.01
                ? a.boundingBox.midY > b.boundingBox.midY
                : a.boundingBox.minX < b.boundingBox.minX
        }
        .compactMap { $0.topCandidates(1).first?.string }
    print("## p.\(i + 1)")
    print(lines.joined(separator: "\n"))
}
