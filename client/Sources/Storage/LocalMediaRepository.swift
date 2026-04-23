import AppCore
import Foundation

public actor LocalMediaRepository: JobsStore {
    private let storageURL: URL
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    public init(storageURL: URL? = nil) {
        let defaultURL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "xdownloader_jobs.json")
        self.storageURL = storageURL ?? defaultURL
        encoder.dateEncodingStrategy = .iso8601
        decoder.dateDecodingStrategy = .iso8601
    }

    public func saveJobs(_ jobs: [Job]) throws {
        try FileManager.default.createDirectory(
            at: storageURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let data = try encoder.encode(jobs)
        try data.write(to: storageURL, options: .atomic)
    }

    public func loadJobs() throws -> [Job] {
        guard FileManager.default.fileExists(atPath: storageURL.path()) else {
            return []
        }
        let data = try Data(contentsOf: storageURL)
        return try decoder.decode([Job].self, from: data)
    }
}
