import AppCore
import Foundation
import Storage
import Testing

@Test func localAppSettingsRepositoryReturnsDefaultsWhenMissing() async throws {
    let url = FileManager.default.temporaryDirectory.appending(path: "xdl-settings-\(UUID().uuidString).json")
    let repository = LocalAppSettingsRepository(storageURL: url)

    let settings = try repository.loadSettings()

    #expect(repository.hasSavedSettings == false)
    #expect(settings.apiBaseURL == URL(string: "http://127.0.0.1:8000")!)
}

@Test func localAppSettingsRepositoryPersistsLocalBackendSecret() async throws {
    let url = FileManager.default.temporaryDirectory.appending(path: "xdl-settings-\(UUID().uuidString).json")
    defer { try? FileManager.default.removeItem(at: url) }
    let repository = LocalAppSettingsRepository(storageURL: url)
    let settings = AppSettings(localBackendSecret: "secret-1")

    try repository.saveSettings(settings)
    let loaded = try repository.loadSettings()
    let attributes = try FileManager.default.attributesOfItem(atPath: url.path)

    #expect(repository.hasSavedSettings)
    #expect(loaded == settings)
    #expect(attributes[.posixPermissions] as? Int == 0o600)
}

@Test func localAppSettingsRepositoryPersistsAutoSaveCompletedArtifactsPreference() async throws {
    let url = FileManager.default.temporaryDirectory.appending(path: "xdl-settings-\(UUID().uuidString).json")
    defer { try? FileManager.default.removeItem(at: url) }
    let repository = LocalAppSettingsRepository(storageURL: url)
    let settings = AppSettings(autoSaveCompletedArtifactsToPhotos: true)

    try repository.saveSettings(settings)
    let loaded = try repository.loadSettings()

    #expect(loaded.autoSaveCompletedArtifactsToPhotos)
}

@Test func localAppSettingsRepositoryKeepsCustomStorageFilesIsolated() async throws {
    let localURL = FileManager.default.temporaryDirectory.appending(path: "xdl-local-settings-\(UUID().uuidString).json")
    let cloudURL = FileManager.default.temporaryDirectory.appending(path: "xdl-cloud-settings-\(UUID().uuidString).json")
    defer {
        try? FileManager.default.removeItem(at: localURL)
        try? FileManager.default.removeItem(at: cloudURL)
    }
    let localRepository = LocalAppSettingsRepository(storageURL: localURL)
    let cloudRepository = LocalAppSettingsRepository(storageURL: cloudURL)
    let localSettings = AppSettings(apiBaseURL: URL(string: "http://127.0.0.1:18767")!, localBackendSecret: "local-secret")
    let cloudSettings = AppSettings(apiBaseURL: URL(string: "https://cloud.example.com:18767")!, bootstrapCode: "cloud-code")

    try localRepository.saveSettings(localSettings)
    try cloudRepository.saveSettings(cloudSettings)
    let loadedLocal = try localRepository.loadSettings()
    let loadedCloud = try cloudRepository.loadSettings()
    let cloudAttributes = try FileManager.default.attributesOfItem(atPath: cloudURL.path)

    #expect(loadedLocal.apiBaseURL == URL(string: "http://127.0.0.1:18767")!)
    #expect(loadedLocal.localBackendSecret == "local-secret")
    #expect(loadedLocal.bootstrapCode == nil)
    #expect(loadedCloud.apiBaseURL == URL(string: "https://cloud.example.com:18767")!)
    #expect(loadedCloud.bootstrapCode == "cloud-code")
    #expect(loadedCloud.localBackendSecret.isEmpty)
    #expect(cloudAttributes[.posixPermissions] as? Int == 0o600)
}
