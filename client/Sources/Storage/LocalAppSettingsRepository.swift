import AppCore
import Foundation

public struct LocalAppSettingsRepository {
    private let storageURL: URL
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    public init(storageURL: URL? = nil) {
        let defaultURL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "xdownloader_settings.json")
        self.storageURL = storageURL ?? defaultURL
    }

    public var hasSavedSettings: Bool {
        FileManager.default.fileExists(atPath: storageURL.path())
    }

    public func loadSettings() throws -> AppSettings {
        guard hasSavedSettings else {
            return AppSettings()
        }
        let data = try Data(contentsOf: storageURL)
        return try decoder.decode(AppSettings.self, from: data)
    }

    public func saveSettings(_ settings: AppSettings) throws {
        try FileManager.default.createDirectory(
            at: storageURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let data = try encoder.encode(settings)
        try data.write(to: storageURL, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: storageURL.path)
    }
}
