// Prints the text of a PDF. Used by build_package.py on macOS.
// Usage: swift tool/pdf2txt.swift file.pdf
import Foundation
import PDFKit

guard CommandLine.arguments.count == 2,
      let doc = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])) else {
    FileHandle.standardError.write("usage: pdf2txt.swift file.pdf\n".data(using: .utf8)!)
    exit(1)
}
for i in 0..<doc.pageCount {
    print(doc.page(at: i)?.string ?? "")
}
