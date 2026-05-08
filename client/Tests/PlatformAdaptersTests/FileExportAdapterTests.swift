import AppCore
import Foundation
import PlatformAdapters
import Testing

@Test func fileExportAdapterAppendsExtensionFromMimeTypeWithoutTruncatingShortFileName() throws {
    let temporaryRoot = FileManager.default.temporaryDirectory.appending(path: "xdl-export-\(UUID().uuidString)", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: temporaryRoot, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporaryRoot) }
    let source = temporaryRoot.appending(path: "source")
    try Data("mp4".utf8).write(to: source)
    let artifact = DownloadedArtifact(temporaryURL: source, fileName: "tweet-video", mimeType: "video/mp4")
    let adapter = FileExportAdapter(downloadsDirectory: temporaryRoot)

    let exported = try adapter.saveArtifact(artifact)

    #expect(exported.lastPathComponent == "tweet-video.mp4")
    #expect(FileManager.default.fileExists(atPath: exported.path))
}

@Test func fileExportAdapterAppendsExtensionFromMimeTypeWhenFileNameHasNoExtension() throws {
    let temporaryRoot = FileManager.default.temporaryDirectory.appending(path: "xdl-export-\(UUID().uuidString)", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: temporaryRoot, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporaryRoot) }
    let source = temporaryRoot.appending(path: "source")
    try Data("mp4".utf8).write(to: source)
    let longName = String(repeating: "李小璐都40岁了看着跟20多岁一样", count: 8)
    let artifact = DownloadedArtifact(temporaryURL: source, fileName: longName, mimeType: "video/mp4")
    let adapter = FileExportAdapter(downloadsDirectory: temporaryRoot)

    let exported = try adapter.saveArtifact(artifact)

    #expect(exported.pathExtension == "mp4")
    #expect(exported.lastPathComponent.utf8.count <= 184)
    #expect(FileManager.default.fileExists(atPath: exported.path))
}

@Test func fileExportAdapterFindsExistingExportedArtifact() throws {
    let temporaryRoot = FileManager.default.temporaryDirectory.appending(path: "xdl-export-\(UUID().uuidString)", directoryHint: .isDirectory)
    let exportDirectory = temporaryRoot.appending(path: "XDownloader", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: exportDirectory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporaryRoot) }
    let exported = exportDirectory.appending(path: "tweet-video.mp4")
    try Data("mp4".utf8).write(to: exported)
    let adapter = FileExportAdapter(downloadsDirectory: temporaryRoot)

    #expect(adapter.existingExportURL(for: "tweet-video", mimeType: "video/mp4", fileSize: 3) == exported)
}

@Test func fileExportAdapterFindsNumberedExistingExportedArtifactBySize() throws {
    let temporaryRoot = FileManager.default.temporaryDirectory.appending(path: "xdl-export-\(UUID().uuidString)", directoryHint: .isDirectory)
    let exportDirectory = temporaryRoot.appending(path: "XDownloader", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: exportDirectory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporaryRoot) }
    try Data("old".utf8).write(to: exportDirectory.appending(path: "tweet-video.mp4"))
    let numberedExport = exportDirectory.appending(path: "tweet-video (1).mp4")
    try Data("new-file".utf8).write(to: numberedExport)
    let adapter = FileExportAdapter(downloadsDirectory: temporaryRoot)

    #expect(adapter.existingExportURL(for: "tweet-video", mimeType: "video/mp4", fileSize: 8) == numberedExport)
}

@Test func fileExportAdapterDoesNotMatchSameNameWithDifferentSize() throws {
    let temporaryRoot = FileManager.default.temporaryDirectory.appending(path: "xdl-export-\(UUID().uuidString)", directoryHint: .isDirectory)
    let exportDirectory = temporaryRoot.appending(path: "XDownloader", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: exportDirectory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporaryRoot) }
    try Data("old".utf8).write(to: exportDirectory.appending(path: "tweet-video.mp4"))
    let adapter = FileExportAdapter(downloadsDirectory: temporaryRoot)

    #expect(adapter.existingExportURL(for: "tweet-video", mimeType: "video/mp4", fileSize: 8) == nil)
}

@Test func fileExportAdapterReturnsMultipleExistingExportCandidatesBySize() throws {
    let temporaryRoot = FileManager.default.temporaryDirectory.appending(path: "xdl-export-\(UUID().uuidString)", directoryHint: .isDirectory)
    let exportDirectory = temporaryRoot.appending(path: "XDownloader", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: exportDirectory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporaryRoot) }
    let first = exportDirectory.appending(path: "tweet-video.mp4")
    let second = exportDirectory.appending(path: "tweet-video (1).mp4")
    try Data("same".utf8).write(to: first)
    try Data("same".utf8).write(to: second)
    let adapter = FileExportAdapter(downloadsDirectory: temporaryRoot)

    #expect(adapter.existingExportCandidates(for: "tweet-video", mimeType: "video/mp4", fileSize: 4) == [first, second])
}

@Test func fileExportAdapterKeepsExistingExtensionWhenTruncatingLongFileName() throws {
    let temporaryRoot = FileManager.default.temporaryDirectory.appending(path: "xdl-export-\(UUID().uuidString)", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: temporaryRoot, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporaryRoot) }
    let source = temporaryRoot.appending(path: "source")
    try Data("mp4".utf8).write(to: source)
    let longName = String(repeating: "视频标题很长", count: 20) + ".mp4"
    let artifact = DownloadedArtifact(temporaryURL: source, fileName: longName, mimeType: "video/mp4")
    let adapter = FileExportAdapter(downloadsDirectory: temporaryRoot)

    let exported = try adapter.saveArtifact(artifact)

    #expect(exported.pathExtension == "mp4")
    #expect(FileManager.default.fileExists(atPath: exported.path))
}
