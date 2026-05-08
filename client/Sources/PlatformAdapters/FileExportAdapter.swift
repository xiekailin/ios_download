import AppCore
import Foundation
#if os(macOS)
import AppKit
#endif

public struct FileExportAdapter: Sendable {
    private let downloadsDirectory: URL

    public init(downloadsDirectory: URL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]) {
        self.downloadsDirectory = downloadsDirectory
    }

    public func suggestedExportURL(for fileName: String) -> URL {
        downloadsDirectory.appending(path: fileName)
    }

    public func existingExportURL(for fileName: String, mimeType: String?, fileSize: Int) -> URL? {
        existingExportCandidates(for: fileName, mimeType: mimeType, fileSize: fileSize).first
    }

    public func existingExportCandidates(for fileName: String, mimeType: String?, fileSize: Int) -> [URL] {
        exportCandidateURLs(for: fileName, mimeType: mimeType).filter { url in
            guard FileManager.default.fileExists(atPath: url.path) else { return false }
            return ((try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? -1) == fileSize
        }
    }

    public func saveArtifact(_ artifact: DownloadedArtifact) throws -> URL {
        let directory = downloadsDirectory.appending(path: "XDownloader", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try validateDirectory(directory, under: downloadsDirectory)
        let destination = try moveArtifact(artifact, to: directory)
        return destination
    }

    #if os(macOS)
    @MainActor
    public func revealInFinder(_ url: URL) {
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    @MainActor
    @discardableResult
    public func copyFileToPasteboard(_ url: URL) -> Bool {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        return pasteboard.writeObjects([url as NSURL])
    }

    @MainActor
    @discardableResult
    public func openInDefaultApp(_ url: URL) -> Bool {
        NSWorkspace.shared.open(url)
    }
    #endif

    private func moveArtifact(_ artifact: DownloadedArtifact, to directory: URL) throws -> URL {
        for candidate in exportCandidateURLs(for: artifact.fileName, mimeType: artifact.mimeType, directory: directory) {
            do {
                try FileManager.default.moveItem(at: artifact.temporaryURL, to: candidate)
                return candidate
            } catch CocoaError.fileWriteFileExists {
                continue
            }
        }
        throw CocoaError(.fileWriteFileExists)
    }

    private func exportCandidateURLs(for fileName: String, mimeType: String?, directory: URL? = nil) -> [URL] {
        let directory = directory ?? downloadsDirectory.appending(path: "XDownloader", directoryHint: .isDirectory)
        let sanitized = sanitizedFileName(fileName, mimeType: mimeType)
        let baseURL = directory.appending(path: sanitized)
        let fileExtension = baseURL.pathExtension
        let stem = baseURL.deletingPathExtension().lastPathComponent
        return (0..<1000).map { index in
            if index == 0 {
                return baseURL
            }
            let candidateName = fileExtension.isEmpty ? "\(stem) (\(index))" : "\(stem) (\(index)).\(fileExtension)"
            return directory.appending(path: candidateName)
        }
    }

    private func validateDirectory(_ directory: URL, under parent: URL) throws {
        let resolvedDirectory = directory.resolvingSymlinksInPath().standardizedFileURL
        let resolvedParent = parent.resolvingSymlinksInPath().standardizedFileURL
        guard resolvedDirectory.path.hasPrefix(resolvedParent.path + "/") else {
            throw CocoaError(.fileWriteInvalidFileName)
        }
    }

    private func sanitizedFileName(_ fileName: String, mimeType: String?) -> String {
        let invalidCharacters = CharacterSet(charactersIn: "/:\\").union(.controlCharacters)
        let parts = fileName.components(separatedBy: invalidCharacters)
        var cleaned = parts.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
        cleaned = cleaned.trimmingCharacters(in: CharacterSet(charactersIn: "."))
        if cleaned.isEmpty || cleaned.hasPrefix(".") {
            cleaned = "download"
        }
        let existingExtension = URL(fileURLWithPath: cleaned).pathExtension.lowercased()
        let extensionToKeep = existingExtension.isEmpty ? fileExtension(for: mimeType) : existingExtension
        let stem = existingExtension.isEmpty ? cleaned : String(cleaned.dropLast(existingExtension.count + 1))
        let maxStemBytes = extensionToKeep.isEmpty ? 180 : max(1, 179 - extensionToKeep.utf8.count)
        var result = ""
        var byteCount = 0
        for character in stem {
            let count = String(character).utf8.count
            guard byteCount + count <= maxStemBytes else { break }
            result.append(character)
            byteCount += count
        }
        if result.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            result = "download"
        }
        return extensionToKeep.isEmpty ? result : "\(result).\(extensionToKeep)"
    }

    private func fileExtension(for mimeType: String?) -> String {
        switch mimeType?.split(separator: ";", maxSplits: 1).first?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "video/mp4", "application/mp4":
            return "mp4"
        case "video/quicktime":
            return "mov"
        case "video/webm":
            return "webm"
        case "audio/mpeg":
            return "mp3"
        case "audio/mp4", "audio/x-m4a":
            return "m4a"
        case "image/jpeg":
            return "jpg"
        case "image/png":
            return "png"
        case "image/webp":
            return "webp"
        case "image/gif":
            return "gif"
        default:
            return ""
        }
    }
}
