import Foundation

public struct FileExportAdapter: Sendable {
    public init() {}

    public func suggestedExportURL(for fileName: String) -> URL {
        FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
            .appending(path: fileName)
    }
}
