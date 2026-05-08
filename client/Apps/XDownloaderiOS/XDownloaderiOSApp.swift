import AppCore
import Foundation
import Networking
import Photos
import SharedUI
import Storage
import SwiftUI
import UIKit

private enum ArtifactLoadState: Equatable {
    case loading
    case loaded([ArtifactSummary])
    case failed(String)
}

private enum ArtifactActionState: Equatable {
    case preparingShare(ArtifactSummary.ID)
    case savingToPhotos(ArtifactSummary.ID)

    var artifactID: ArtifactSummary.ID {
        switch self {
        case let .preparingShare(id), let .savingToPhotos(id):
            id
        }
    }
}

private enum PhotoSaveProgressState: Equatable {
    case downloading(ArtifactDownloadProgress)
    case savingToPhotos
    case cleaningServer
}

private let defaultCloudBaseURL = URL(string: "http://124.221.197.94:18767")!
private let defaultCloudBootstrapCode = "AzRnrZi3-AMmP8dgkusZURfzPk5qu5NQ"

private enum ConsoleTheme {
    static let backgroundTop = Color(red: 0.04, green: 0.045, blue: 0.052)
    static let backgroundBottom = Color(red: 0.08, green: 0.085, blue: 0.095)
    static let surface = Color(red: 0.12, green: 0.13, blue: 0.145)
    static let surfaceElevated = Color(red: 0.16, green: 0.17, blue: 0.185)
    static let surfaceInset = Color(red: 0.075, green: 0.082, blue: 0.095)
    static let border = Color.white.opacity(0.1)
    static let textPrimary = Color(red: 0.94, green: 0.95, blue: 0.96)
    static let textSecondary = Color(red: 0.66, green: 0.70, blue: 0.74)
    static let textMuted = Color(red: 0.45, green: 0.49, blue: 0.53)
    static let iceBlue = Color(red: 0.48, green: 0.82, blue: 0.94)
    static let champagne = Color(red: 0.87, green: 0.73, blue: 0.48)
    static let success = Color(red: 0.43, green: 0.72, blue: 0.55)
    static let danger = Color(red: 0.86, green: 0.34, blue: 0.36)
}

private enum ConsoleThemeMode: String, CaseIterable, Identifiable {
    case premium
    case neon

    var id: String { rawValue }

    var title: String {
        switch self {
        case .premium: "高级工具风"
        case .neon: "霓虹灯风"
        }
    }

    var backgroundTop: Color {
        switch self {
        case .premium: ConsoleTheme.backgroundTop
        case .neon: Color(red: 0.035, green: 0.025, blue: 0.075)
        }
    }

    var backgroundBottom: Color {
        switch self {
        case .premium: ConsoleTheme.backgroundBottom
        case .neon: Color(red: 0.07, green: 0.02, blue: 0.095)
        }
    }

    var surface: Color {
        switch self {
        case .premium: ConsoleTheme.surface
        case .neon: Color(red: 0.105, green: 0.075, blue: 0.145)
        }
    }

    var surfaceElevated: Color {
        switch self {
        case .premium: ConsoleTheme.surfaceElevated
        case .neon: Color(red: 0.14, green: 0.10, blue: 0.20)
        }
    }

    var surfaceInset: Color {
        switch self {
        case .premium: ConsoleTheme.surfaceInset
        case .neon: Color(red: 0.045, green: 0.035, blue: 0.085)
        }
    }

    var border: Color {
        switch self {
        case .premium: ConsoleTheme.border
        case .neon: Color(red: 0.78, green: 0.36, blue: 1).opacity(0.24)
        }
    }

    var textPrimary: Color { ConsoleTheme.textPrimary }
    var textSecondary: Color { ConsoleTheme.textSecondary }
    var textMuted: Color { ConsoleTheme.textMuted }

    var primaryAccent: Color {
        switch self {
        case .premium: ConsoleTheme.iceBlue
        case .neon: Color(red: 0.31, green: 0.95, blue: 1)
        }
    }

    var secondaryAccent: Color {
        switch self {
        case .premium: ConsoleTheme.champagne
        case .neon: Color(red: 1, green: 0.36, blue: 0.86)
        }
    }

    var success: Color { ConsoleTheme.success }
    var danger: Color { ConsoleTheme.danger }
}

private enum ConsoleTab: String, CaseIterable, Identifiable {
    case tasks
    case history
    case settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .tasks: "任务"
        case .history: "历史"
        case .settings: "设置"
        }
    }

    var systemImage: String {
        switch self {
        case .tasks: "waveform.path.ecg"
        case .history: "clock"
        case .settings: "gearshape"
        }
    }
}

private struct ConsoleBackground: View {
    let theme: ConsoleThemeMode

    var body: some View {
        LinearGradient(
            colors: [theme.backgroundTop, theme.backgroundBottom],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
        .overlay(alignment: .topTrailing) {
            Circle()
                .fill(theme.primaryAccent.opacity(theme == .premium ? 0.12 : 0.18))
                .frame(width: 240, height: 240)
                .blur(radius: 58)
                .offset(x: 82, y: -108)
        }
        .overlay(alignment: .bottomLeading) {
            Circle()
                .fill(theme.secondaryAccent.opacity(theme == .premium ? 0.08 : 0.14))
                .frame(width: 210, height: 210)
                .blur(radius: 64)
                .offset(x: -95, y: 65)
        }
    }
}

private extension View {
    func consoleCard(theme: ConsoleThemeMode = .premium, cornerRadius: CGFloat = 24) -> some View {
        self
            .background(theme.surface.opacity(0.94), in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(theme.border, lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.24), radius: 22, x: 0, y: 16)
    }

    func consoleInset(theme: ConsoleThemeMode = .premium, cornerRadius: CGFloat = 16) -> some View {
        self
            .background(theme.surfaceInset, in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(theme.border, lineWidth: 1)
            )
    }
}

private struct ConsolePrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    let theme: ConsoleThemeMode

    init(theme: ConsoleThemeMode = .premium) {
        self.theme = theme
    }

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(Color.black.opacity(isEnabled ? 0.88 : 0.42))
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(
                LinearGradient(
                    colors: [theme.primaryAccent, theme.primaryAccent.opacity(0.72)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                ),
                in: RoundedRectangle(cornerRadius: 18, style: .continuous)
            )
            .saturation(isEnabled ? 1 : 0.1)
            .opacity(isEnabled ? (configuration.isPressed ? 0.78 : 1) : 0.45)
    }
}

private struct ConsoleSecondaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    let theme: ConsoleThemeMode

    init(theme: ConsoleThemeMode = .premium) {
        self.theme = theme
    }

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(isEnabled ? theme.textPrimary : theme.textMuted)
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .background(theme.surfaceElevated, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .stroke(theme.border, lineWidth: 1)
            )
            .opacity(isEnabled ? (configuration.isPressed ? 0.76 : 1) : 0.5)
    }
}

private struct ConsoleThemeButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    let isSelected: Bool
    let theme: ConsoleThemeMode

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(isSelected ? Color.black.opacity(0.88) : (isEnabled ? theme.textPrimary : theme.textMuted))
            .padding(.horizontal, isSelected ? 16 : 14)
            .padding(.vertical, isSelected ? 12 : 11)
            .background(background, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .stroke(isSelected ? Color.clear : theme.border, lineWidth: 1)
            )
            .saturation(isEnabled ? 1 : 0.1)
            .opacity(isEnabled ? (configuration.isPressed ? 0.76 : 1) : 0.5)
    }

    private var background: AnyShapeStyle {
        if isSelected {
            AnyShapeStyle(LinearGradient(
                colors: [theme.primaryAccent, theme.primaryAccent.opacity(0.72)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            ))
        } else {
            AnyShapeStyle(theme.surfaceElevated)
        }
    }
}

private enum ArtifactActionError: LocalizedError {
    case missingRegistration
    case unsupportedPhotoLibraryType
    case photoLibraryDenied

    var errorDescription: String? {
        switch self {
        case .missingRegistration:
            "设备初始化失败，请重试。"
        case .unsupportedPhotoLibraryType:
            "该文件类型不能保存到相册，请使用分享。"
        case .photoLibraryDenied:
            "请在系统设置中允许 XDownloader 保存到相册。"
        }
    }
}

private enum PhotoLibraryResourceType {
    case image
    case video
}

private func canSaveArtifactToPhotos(_ artifact: ArtifactSummary) -> Bool {
    switch artifact.fileName.split(separator: ".").last?.lowercased() {
    case "jpg", "jpeg", "png", "heic", "heif", "gif", "tiff", "webp", "mp4", "mov", "m4v":
        true
    default:
        false
    }
}

private enum PhotoLibraryWriter {
    static func save(_ url: URL, resourceType: PhotoLibraryResourceType) async throws -> Bool {
        var didCreateRequest = false
        try await PHPhotoLibrary.shared().performChanges {
            let request: PHAssetChangeRequest?
            switch resourceType {
            case .video:
                request = PHAssetChangeRequest.creationRequestForAssetFromVideo(atFileURL: url)
            case .image:
                request = PHAssetChangeRequest.creationRequestForAssetFromImage(atFileURL: url)
            }
            request?.creationDate = Date()
            didCreateRequest = request != nil
        }
        return didCreateRequest
    }
}

@main
struct XDownloaderiOSApp: App {
    @State private var store: JobStore
    @State private var controller: AppController
    @State private var hasServerSettings: Bool
    @State private var preferredDeepLinkMode: DeepLinkSubmissionMode = .download

    init() {
        let repository = LocalAppSettingsRepository()
        let settings = Self.loadInitialSettings(repository: repository)
        self._store = State(initialValue: JobStore(settings: settings))
        self._controller = State(initialValue: Self.makeController(settings: settings))
        self._hasServerSettings = State(initialValue: true)
    }

    private static var applicationSupportURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    }

    private static func loadInitialSettings(repository: LocalAppSettingsRepository) -> AppSettings {
        var settings = (try? repository.loadSettings()) ?? AppSettings()
        if !repository.hasSavedSettings || settings.apiBaseURL == AppSettings().apiBaseURL || settings.bootstrapCode?.isEmpty != false {
            settings.apiBaseURL = defaultCloudBaseURL
            settings.bootstrapCode = defaultCloudBootstrapCode
        }
        settings.autoSaveCompletedArtifactsToPhotos = true
        try? repository.saveSettings(settings)
        return settings
    }

    private static func makeController(settings: AppSettings) -> AppController {
        let configuration = (try? ServerConfiguration.parseBaseURL(settings.apiBaseURL.absoluteString))
        let storageID = configuration?.storageID ?? "unconfigured"
        return AppController(
            apiClient: APIClient(baseURL: configuration?.url ?? settings.apiBaseURL),
            registrationStore: LocalRegistrationRepository(
                storageURL: applicationSupportURL.appending(path: "xdownloader_device_\(storageID).json"),
                account: "default_\(storageID)"
            ),
            jobsStore: LocalMediaRepository(storageURL: applicationSupportURL.appending(path: "xdownloader_jobs_\(storageID).json")),
            deviceName: "iPhone",
            platform: "ios",
            appVersion: "0.1.0"
        )
    }

    var body: some Scene {
        WindowGroup {
            JobListScreen(
                title: "视频下载",
                store: store,
                controller: $controller,
                hasServerSettings: $hasServerSettings,
                preferredDeepLinkMode: $preferredDeepLinkMode,
                makeController: Self.makeController(settings:)
            )
            .preferredColorScheme(.dark)
            .task {
                guard hasServerSettings else { return }
                await controller.start(store: store)
            }
            .onOpenURL { url in
                guard let mode = controller.handleDeepLink(url, store: store) else { return }
                preferredDeepLinkMode = mode
            }
        }
    }
}

private struct JobListScreen: View {
    let title: String
    @Bindable var store: JobStore
    @Binding var controller: AppController
    @Binding var hasServerSettings: Bool
    @Binding var preferredDeepLinkMode: DeepLinkSubmissionMode
    let makeController: (AppSettings) -> AppController
    @State private var pendingDeleteJob: Job?
    @State private var artifactStates: [Job.ID: ArtifactLoadState] = [:]
    @State private var sharedArtifactURLs: [ArtifactSummary.ID: URL] = [:]
    @State private var activeArtifactAction: ArtifactActionState?
    @State private var photoSaveProgressStates: [ArtifactSummary.ID: PhotoSaveProgressState] = [:]
    @State private var lastPhotoSaveProgressUpdateAt: [ArtifactSummary.ID: Date] = [:]
    @State private var successMessage: String?
    @State private var autoSaveAttemptedJobIDs: Set<Job.ID> = []
    @State private var autoSaveFailedJobIDs: Set<Job.ID> = []
    @State private var autoSaveInFlightJobID: Job.ID?
    @State private var serverBaseURLDraft = ""
    @State private var bootstrapCodeDraft = ""
    @State private var isEditingServerSettings = false
    @State private var selectedConsoleTab: ConsoleTab = .tasks
    @State private var themeMode: ConsoleThemeMode = .premium
    private let settingsRepository = LocalAppSettingsRepository()

    private var activeJobs: [Job] {
        store.jobs.filter { !$0.status.isTerminal }
    }

    private var historyJobs: [Job] {
        store.jobs.filter { $0.status.isTerminal }
    }

    private var completedJobs: [Job] {
        store.jobs.filter { $0.status == .completed }
    }

    private var queueMetricText: String {
        String(format: "%02d", activeJobs.count)
    }

    private var speedMetricText: String {
        activeJobs.compactMap(\.speedText).first ?? "--"
    }

    private var completedMetricText: String {
        String(format: "%02d", completedJobs.count)
    }

    private var isAnyArtifactOperationActive: Bool {
        activeArtifactAction != nil || autoSaveInFlightJobID != nil
    }

    private var savingStatusText: String? {
        if let autoSaveInFlightJobID, let job = store.job(id: autoSaveInFlightJobID) {
            if case let .savingToPhotos(artifactID) = activeArtifactAction {
                return "正在自动保存：\(saveProgressLabel(for: artifactID))"
            }
            return "正在自动保存：\(job.mediaTitle ?? "下载文件")"
        }
        guard case let .savingToPhotos(artifactID) = activeArtifactAction else { return nil }
        return "正在保存到相册：\(saveProgressLabel(for: artifactID))"
    }

    private func saveProgressLabel(for artifactID: ArtifactSummary.ID) -> String {
        switch photoSaveProgressStates[artifactID] {
        case let .downloading(progress):
            if let fraction = progress.fraction {
                return "传输到 iPhone \(Int((fraction * 100).rounded()))%"
            }
            return "传输到 iPhone"
        case .savingToPhotos:
            return "写入相册"
        case .cleaningServer:
            return "清理服务器文件"
        case nil:
            return "准备中"
        }
    }

    private func refreshArtifacts(for job: Job, force: Bool = false) async {
        guard job.status == .completed, (job.jobType == .audioSeparation || job.artifactID != nil) else {
            return
        }
        if !force, case let .loaded(artifacts) = artifactStates[job.id], !artifacts.isEmpty {
            return
        }
        guard let token = store.registration?.accessToken else {
            artifactStates[job.id] = .failed(ArtifactActionError.missingRegistration.localizedDescription)
            return
        }
        artifactStates[job.id] = .loading
        do {
            artifactStates[job.id] = .loaded(try await controller.listJobArtifacts(jobID: job.id, token: token))
            store.setError(nil)
        } catch {
            artifactStates[job.id] = .failed(error.localizedDescription)
        }
    }

    private func prepareArtifactForSharing(_ artifact: ArtifactSummary) async {
        guard activeArtifactAction == nil else { return }
        activeArtifactAction = .preparingShare(artifact.id)
        successMessage = nil
        defer { activeArtifactAction = nil }
        do {
            let url = try await localShareURL(for: artifact)
            sharedArtifactURLs[artifact.id] = url
            successMessage = "文件已准备好，可以分享。"
            store.setError(nil)
        } catch {
            successMessage = nil
            store.setError(error.localizedDescription)
        }
    }

    private func saveArtifactToPhotos(_ artifact: ArtifactSummary, jobID: Job.ID) async {
        _ = await saveArtifactToPhotos(artifact, jobID: jobID, isAutomatic: false)
    }

    private func saveArtifactToPhotos(_ artifact: ArtifactSummary, jobID: Job.ID, isAutomatic: Bool) async -> Bool {
        guard activeArtifactAction == nil else { return false }
        let backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "SaveArtifactToPhotos")
        activeArtifactAction = .savingToPhotos(artifact.id)
        successMessage = nil
        defer {
            activeArtifactAction = nil
            photoSaveProgressStates.removeValue(forKey: artifact.id)
            lastPhotoSaveProgressUpdateAt.removeValue(forKey: artifact.id)
            if backgroundTask != .invalid {
                UIApplication.shared.endBackgroundTask(backgroundTask)
            }
        }
        do {
            photoSaveProgressStates[artifact.id] = .downloading(ArtifactDownloadProgress(
                receivedBytes: 0,
                totalBytes: nil,
                fraction: nil,
                bytesPerSecond: nil,
                etaSeconds: nil
            ))
            let url = try await localShareURL(for: artifact) { progress in
                await updatePhotoSaveDownloadProgress(progress, for: artifact.id)
            }
            photoSaveProgressStates[artifact.id] = .savingToPhotos
            try await saveToPhotoLibrary(url)
            photoSaveProgressStates[artifact.id] = .cleaningServer
            removeSharedArtifactCache(for: artifact, url: url)
            if await controller.deleteArtifact(id: artifact.id, store: store) {
                removeArtifact(artifact, from: jobID)
                successMessage = isAutomatic ? "下载完成，已自动保存到相册。" : "已保存到相册，服务器文件已清理。"
                await controller.refreshJobs(store: store)
                return true
            } else {
                successMessage = isAutomatic ? "已自动保存到相册，但服务器文件清理失败。" : "已保存到相册，但服务器文件清理失败。"
                return true
            }
        } catch {
            successMessage = nil
            store.setError(error.localizedDescription)
            return false
        }
    }

    private func autoSaveCompletedArtifactsIfNeeded() {
        guard store.settings.autoSaveCompletedArtifactsToPhotos,
              store.registration?.accessToken != nil,
              !isAnyArtifactOperationActive,
              autoSaveInFlightJobID == nil
        else { return }
        guard let job = store.jobs.first(where: { job in
            job.status == .completed
                && (job.jobType == .audioSeparation || job.artifactID != nil)
                && !autoSaveAttemptedJobIDs.contains(job.id)
                && !autoSaveFailedJobIDs.contains(job.id)
        }) else { return }
        autoSaveInFlightJobID = job.id
        Task { await autoSaveCompletedArtifacts(for: job) }
    }

    private func autoSaveCompletedArtifacts(for job: Job) async {
        defer {
            autoSaveInFlightJobID = nil
            autoSaveCompletedArtifactsIfNeeded()
        }
        await refreshArtifacts(for: job, force: false)
        guard case let .loaded(artifacts)? = artifactStates[job.id] else {
            autoSaveFailedJobIDs.insert(job.id)
            return
        }
        let saveableArtifacts = artifacts.filter(canSaveArtifactToPhotos)
        guard !saveableArtifacts.isEmpty else {
            autoSaveAttemptedJobIDs.insert(job.id)
            return
        }
        for artifact in saveableArtifacts {
            let didSave = await saveArtifactToPhotos(artifact, jobID: job.id, isAutomatic: true)
            if !didSave {
                autoSaveFailedJobIDs.insert(job.id)
                return
            }
        }
        autoSaveAttemptedJobIDs.insert(job.id)
    }

    private func updatePhotoSaveDownloadProgress(_ progress: ArtifactDownloadProgress, for artifactID: ArtifactSummary.ID) {
        if let state = photoSaveProgressStates[artifactID], state != .downloading(progress) {
            guard case .downloading = state else { return }
        }
        let now = Date()
        if let lastUpdate = lastPhotoSaveProgressUpdateAt[artifactID], now.timeIntervalSince(lastUpdate) < 0.2, progress.fraction != 1 {
            return
        }
        lastPhotoSaveProgressUpdateAt[artifactID] = now
        photoSaveProgressStates[artifactID] = .downloading(progress)
    }

    private func saveToPhotoLibrary(_ url: URL) async throws {
        guard let resourceType = photoLibraryResourceType(for: url) else {
            throw ArtifactActionError.unsupportedPhotoLibraryType
        }
        let status = PHPhotoLibrary.authorizationStatus(for: .addOnly)
        let authorized = status == .authorized || status == .limited
            ? status
            : await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard authorized == .authorized || authorized == .limited else {
            throw ArtifactActionError.photoLibraryDenied
        }
        guard try await PhotoLibraryWriter.save(url, resourceType: resourceType) else {
            throw ArtifactActionError.unsupportedPhotoLibraryType
        }
    }

    private func removeArtifact(_ artifact: ArtifactSummary, from jobID: Job.ID) {
        if case let .loaded(artifacts)? = artifactStates[jobID] {
            artifactStates[jobID] = .loaded(artifacts.filter { $0.id != artifact.id })
        }
        removeSharedArtifactCache(for: artifact)
    }

    private func removeSharedArtifactCache(for artifact: ArtifactSummary, url: URL? = nil) {
        let cachedURL = sharedArtifactURLs.removeValue(forKey: artifact.id) ?? url
        if let cachedURL {
            try? FileManager.default.removeItem(at: cachedURL.deletingLastPathComponent())
        }
    }

    private func photoLibraryResourceType(for url: URL) -> PhotoLibraryResourceType? {
        switch url.pathExtension.lowercased() {
        case "jpg", "jpeg", "png", "heic", "heif", "gif", "tiff", "webp":
            .image
        case "mp4", "mov", "m4v":
            .video
        default:
            nil
        }
    }

    private func localShareURL(
        for artifact: ArtifactSummary,
        onProgress: (@Sendable (ArtifactDownloadProgress) async -> Void)? = nil
    ) async throws -> URL {
        if let cachedURL = sharedArtifactURLs[artifact.id], FileManager.default.fileExists(atPath: cachedURL.path) {
            return cachedURL
        }
        guard let token = store.registration?.accessToken else {
            throw ArtifactActionError.missingRegistration
        }
        let downloaded: DownloadedArtifact
        if let onProgress {
            downloaded = try await controller.downloadArtifact(id: artifact.id, token: token, onProgress: onProgress)
        } else {
            downloaded = try await controller.downloadArtifact(id: artifact.id, token: token)
        }
        let cachesDirectory = try FileManager.default.url(
            for: .cachesDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        let directory = cachesDirectory
            .appending(component: "XDownloader", directoryHint: .isDirectory)
            .appending(component: "SharedArtifacts", directoryHint: .isDirectory)
            .appending(component: safeShareDirectoryName(artifact.id), directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let destination = directory.appending(path: safeShareFileName(downloaded.fileName), directoryHint: .notDirectory)
        if FileManager.default.fileExists(atPath: destination.path) {
            try FileManager.default.removeItem(at: destination)
        }
        try FileManager.default.moveItem(at: downloaded.temporaryURL, to: destination)
        return destination
    }

    private func safeShareFileName(_ fileName: String) -> String {
        let invalidCharacters = CharacterSet(charactersIn: "/:\\").union(.controlCharacters)
        let cleaned = fileName.components(separatedBy: invalidCharacters)
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "."))
        return cleaned.isEmpty ? "download" : cleaned
    }

    private func safeShareDirectoryName(_ value: String) -> String {
        let invalidCharacters = CharacterSet(charactersIn: "/:\\").union(.controlCharacters)
        let cleaned = value.components(separatedBy: invalidCharacters)
            .joined(separator: "_")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "."))
        return cleaned.isEmpty ? UUID().uuidString : cleaned
    }

    private func retryJob(_ job: Job) async {
        successMessage = nil
        artifactStates.removeValue(forKey: job.id)
        await controller.retryJob(id: job.id, store: store)
    }

    private func saveAutoSaveCompletedArtifactsPreference(_ isEnabled: Bool) {
        var settings = store.settings
        settings.autoSaveCompletedArtifactsToPhotos = isEnabled
        do {
            try settingsRepository.saveSettings(settings)
            store.setSettings(settings)
            store.setError(nil)
            successMessage = isEnabled ? "已开启完成后自动保存到相册。" : "已关闭自动保存到相册。"
        } catch {
            store.setError(error.localizedDescription)
        }
    }

    private func beginEditingServerSettings() {
        serverBaseURLDraft = hasServerSettings ? store.settings.apiBaseURL.absoluteString : ""
        bootstrapCodeDraft = store.settings.bootstrapCode ?? ""
        isEditingServerSettings = true
        successMessage = nil
    }

    private func saveServerSettings() {
        do {
            let configuration = try ServerConfiguration.parseBaseURL(serverBaseURLDraft)
            let trimmedBootstrapCode = bootstrapCodeDraft.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmedBootstrapCode.isEmpty else {
                store.setError("请输入服务器邀请码。")
                return
            }
            var settings = store.settings
            settings.apiBaseURL = configuration.url
            settings.bootstrapCode = trimmedBootstrapCode
            try settingsRepository.saveSettings(settings)
            store.setSettings(settings)
            store.setRegistration(nil)
            store.setYouTubeCookieStatus(nil)
            controller = makeController(settings)
            hasServerSettings = true
            isEditingServerSettings = false
            serverBaseURLDraft = configuration.url.absoluteString
            bootstrapCodeDraft = trimmedBootstrapCode
            store.setError(nil)
            successMessage = "服务器配置已保存。"
            Task { await controller.start(store: store) }
        } catch {
            store.setError(error.localizedDescription)
        }
    }

    private func clearDraftURL() {
        store.draftURL = ""
        successMessage = nil
        store.setError(nil)
    }

    private func pasteDraftURL() {
        successMessage = nil
        guard let clipboardText = UIPasteboard.general.string,
              let url = ClipboardURLExtractor.firstSupportedURL(in: clipboardText)
        else {
            store.setError("剪贴板中没有支持的链接。")
            return
        }
        store.draftURL = url
        store.setError(nil)
    }

    private func submitCurrentInput() async {
        successMessage = nil
        guard hasServerSettings else {
            store.setError("请先在设置中配置服务器地址和邀请码。")
            selectedConsoleTab = .settings
            return
        }
        if preferredDeepLinkMode == .audio {
            _ = await controller.submitAudioDownloadURL(store: store)
        } else {
            _ = await controller.submitCurrentURL(store: store)
        }
    }

    private var serverCard: some View {
        DashboardCard(theme: themeMode) {
            SectionTitle(title: "服务器", subtitle: hasServerSettings ? "云端下载服务已接入，设备状态会自动同步。" : "请先配置云端 API 地址和邀请码。", theme: themeMode)
            VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("当前地址")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(themeMode.textMuted)
                        .textCase(.uppercase)
                    Text(hasServerSettings ? store.settings.apiBaseURL.absoluteString : "未配置")
                        .font(.footnote.monospaced())
                        .foregroundStyle(themeMode.textPrimary)
                        .textSelection(.enabled)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .consoleInset(theme: themeMode)
                }
                serverSettingsControl
                themeSettingsCard
                autoSaveSettingsCard
                youtubeCookieStatusCard
            }
        }
    }

    private var themeSettingsCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("主题")
                .font(.caption.weight(.semibold))
                .foregroundStyle(themeMode.textMuted)
                .textCase(.uppercase)
            HStack(spacing: 10) {
                ForEach(ConsoleThemeMode.allCases) { mode in
                    Button {
                        withAnimation(.easeInOut(duration: 0.18)) {
                            themeMode = mode
                        }
                    } label: {
                        HStack(spacing: 7) {
                            Circle()
                                .fill(mode.primaryAccent)
                                .frame(width: 9, height: 9)
                            Text(mode.title)
                                .lineLimit(1)
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(ConsoleThemeButtonStyle(isSelected: mode == themeMode, theme: themeMode))
                }
            }
        }
        .padding(12)
        .consoleInset(theme: themeMode)
    }

    private var autoSaveSettingsCard: some View {
        Toggle(isOn: Binding(
            get: { store.settings.autoSaveCompletedArtifactsToPhotos },
            set: { isEnabled in saveAutoSaveCompletedArtifactsPreference(isEnabled) }
        )) {
            VStack(alignment: .leading, spacing: 4) {
                Text("完成后自动保存到相册")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(themeMode.textPrimary)
                Text("下载完成后自动保存可写入相册的视频或图片，并清理服务器文件。")
                    .font(.footnote)
                    .foregroundStyle(themeMode.textSecondary)
            }
        }
        .tint(themeMode.primaryAccent)
        .padding(12)
        .consoleInset(theme: themeMode)
    }

    private var youtubeCookieStatusCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("YouTube 登录 Cookie")
                .font(.caption.weight(.semibold))
                .foregroundStyle(themeMode.textMuted)
                .textCase(.uppercase)
            if let status = store.youtubeCookieStatus {
                Text(status.isConfigured ? "云端已配置 Cookie" : "云端未配置 Cookie")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(themeMode.textPrimary)
                Text(status.isConfigured ? "需要登录验证的 YouTube 链接会自动使用云端 Cookie。" : "请先在 Mac 端上传 YouTube cookies.txt。")
                    .font(.footnote)
                    .foregroundStyle(themeMode.textSecondary)
                if let fileSize = status.fileSize {
                    Text("大小 \(ByteCountFormatter.string(fromByteCount: Int64(fileSize), countStyle: .file))")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(themeMode.textMuted)
                }
            } else {
                Text("尚未检查云端 Cookie 状态。")
                    .font(.footnote)
                    .foregroundStyle(themeMode.textSecondary)
            }
            Button("检查 Cookie 状态") {
                Task {
                    successMessage = nil
                    guard hasServerSettings else {
                        store.setError("请先配置服务器地址和邀请码。")
                        return
                    }
                    if let status = await controller.refreshYouTubeCookieStatus(store: store) {
                        successMessage = status.isConfigured ? "云端 YouTube Cookie 已配置。" : "云端尚未配置 YouTube Cookie。"
                    }
                }
            }
            .buttonStyle(ConsoleSecondaryButtonStyle(theme: themeMode))
            .disabled(!hasServerSettings || store.isLoading)
        }
        .padding(12)
        .consoleInset(theme: themeMode)
    }

    @ViewBuilder
    private var serverSettingsControl: some View {
        if hasServerSettings, !isEditingServerSettings {
            statusRow(systemImage: store.registration == nil ? "key.fill" : "checkmark.seal.fill", title: store.registration == nil ? "服务器已配置" : "设备已初始化", subtitle: store.registration == nil ? "首次创建任务时会自动初始化设备。" : "当前设备已连接到此服务器。")
            Button("修改服务器配置", action: beginEditingServerSettings)
                .buttonStyle(ConsoleSecondaryButtonStyle(theme: themeMode))
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Text("API 地址")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(themeMode.textMuted)
                    .textCase(.uppercase)
                TextField("https://example.com:18767", text: $serverBaseURLDraft)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .foregroundStyle(themeMode.textPrimary)
                    .padding(12)
                    .consoleInset(theme: themeMode)
                    .accessibilityLabel("服务器 API 地址")
                Text("服务器邀请码")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(themeMode.textMuted)
                    .textCase(.uppercase)
                TextField("输入服务器邀请码", text: $bootstrapCodeDraft)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .foregroundStyle(themeMode.textPrimary)
                    .padding(12)
                    .consoleInset(theme: themeMode)
                    .accessibilityLabel("服务器邀请码")
                Button(hasServerSettings ? "保存新服务器配置" : "保存服务器配置", action: saveServerSettings)
                    .buttonStyle(ConsolePrimaryButtonStyle(theme: themeMode))
            }
        }
    }

    private var heroInputCard: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("粘贴链接，即刻解析")
                        .font(.system(size: 28, weight: .black, design: .rounded))
                        .foregroundStyle(themeMode.textPrimary)
                    Text("支持 X / 抖音 / 皮皮虾 / 小红书 / Bilibili / YouTube")
                        .font(.footnote.weight(.medium))
                        .foregroundStyle(themeMode.textSecondary)
                }
                Spacer(minLength: 12)
                Button {
                    selectedConsoleTab = .settings
                } label: {
                    Image(systemName: "slider.horizontal.3")
                        .font(.system(size: 20, weight: .semibold))
                        .foregroundStyle(themeMode.primaryAccent)
                        .frame(width: 50, height: 50)
                        .background(themeMode.surfaceElevated, in: Circle())
                        .overlay(Circle().stroke(themeMode.border, lineWidth: 1))
                        .shadow(color: .black.opacity(0.1), radius: 8, x: 0, y: 4)
                }
                .accessibilityLabel("设置")
            }

            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    Image(systemName: "link")
                        .font(.headline.weight(.semibold))
                        .foregroundStyle(themeMode.primaryAccent)
                    TextField("https://...", text: $store.draftURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .font(.body.monospaced())
                        .foregroundStyle(themeMode.textPrimary)
                        .accessibilityLabel("下载链接")
                    
                    if !store.draftURL.isEmpty {
                        Button(action: clearDraftURL) {
                            Image(systemName: "xmark.circle.fill")
                                .font(.title3.weight(.semibold))
                                .foregroundStyle(themeMode.textMuted.opacity(0.8))
                                .frame(width: 44, height: 44)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("清空链接")
                    }
                    
                    Button(action: pasteDraftURL) {
                        Image(systemName: "doc.on.clipboard.fill")
                            .font(.title3.weight(.semibold))
                            .foregroundStyle(themeMode.primaryAccent)
                            .frame(width: 44, height: 44)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("粘贴链接")
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 14)
                .background(themeMode.surfaceInset)
                .clipShape(RoundedRectangle(cornerRadius: 18))
                .overlay(
                    RoundedRectangle(cornerRadius: 18)
                        .stroke(store.draftURL.isEmpty ? themeMode.border : themeMode.primaryAccent.opacity(0.5), lineWidth: 1.5)
                )
            }

            HStack(spacing: 12) {
                Button {
                    preferredDeepLinkMode = .download
                    Task { await submitCurrentInput() }
                } label: {
                    HStack(spacing: 8) {
                        if store.isLoading, preferredDeepLinkMode == .download {
                            ProgressView()
                                .tint(.black.opacity(0.8))
                        } else {
                            Image(systemName: "arrow.down.circle.fill")
                                .font(.title3)
                        }
                        Text(store.isLoading && preferredDeepLinkMode == .download ? "处理中" : "开始下载")
                            .font(.headline.weight(.bold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                }
                .buttonStyle(ConsolePrimaryButtonStyle(theme: themeMode))
                .disabled(!hasServerSettings || store.isLoading || store.draftURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                Button {
                    preferredDeepLinkMode = .audio
                    Task { await submitCurrentInput() }
                } label: {
                    HStack(spacing: 8) {
                        if store.isLoading, preferredDeepLinkMode == .audio {
                            ProgressView()
                                .tint(themeMode.textPrimary)
                        } else {
                            Image(systemName: "music.note")
                                .font(.title3)
                        }
                        Text(store.isLoading && preferredDeepLinkMode == .audio ? "提取中" : "提取音频")
                            .font(.headline.weight(.bold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                }
                .buttonStyle(ConsoleSecondaryButtonStyle(theme: themeMode))
                .disabled(!hasServerSettings || store.isLoading || store.draftURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }

            Button {
                Task {
                    successMessage = nil
                    autoSaveFailedJobIDs.removeAll()
                    await controller.refreshJobs(store: store)
                }
            } label: {
                Label("刷新任务状态", systemImage: "arrow.clockwise")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
            }
            .buttonStyle(ConsoleSecondaryButtonStyle(theme: themeMode))
            .disabled(store.isLoading)
        }
        .padding(24)
        .background(
            RoundedRectangle(cornerRadius: 32, style: .continuous)
                .fill(themeMode.surface.opacity(0.95))
                .shadow(color: themeMode == .premium ? .black.opacity(0.15) : themeMode.primaryAccent.opacity(0.1), radius: 20, x: 0, y: 10)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 32, style: .continuous)
                .stroke(themeMode.border, lineWidth: 1)
        )
    }

    private var metricsGrid: some View {
        HStack(spacing: 14) {
            ConsoleMetricCard(title: "队列", value: queueMetricText, theme: themeMode)
            ConsoleMetricCard(title: "速度", value: speedMetricText, theme: themeMode)
            ConsoleMetricCard(title: "完成", value: completedMetricText, highlight: true, theme: themeMode)
        }
    }

    private var tasksContent: some View {
        VStack(alignment: .leading, spacing: 22) {
            heroInputCard
            metricsGrid
            processingSection
            insightCards
        }
    }

    private var historyContent: some View {
        DashboardCard(theme: themeMode) {
            SectionTitle(title: "历史", subtitle: historyJobs.isEmpty ? "暂无已完成、失败或取消的任务。" : "已完成和已结束的下载记录。", theme: themeMode)
            if historyJobs.isEmpty {
                emptyState(icon: "clock", title: "还没有历史", subtitle: "完成或取消的任务会显示在这里。")
            } else {
                LazyVStack(spacing: 12) {
                    ForEach(historyJobs) { job in
                        JobSummaryCard(job: job, theme: themeMode, isActionDisabled: store.isLoading || isAnyArtifactOperationActive, onCancel: {
                            Task { await controller.cancelJob(id: job.id, store: store) }
                        }, onDelete: {
                            pendingDeleteJob = job
                        })
                    }
                }
            }
        }
    }

    private var settingsContent: some View {
        VStack(alignment: .leading, spacing: 16) {
            serverCard
        }
    }

    private var processingSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text("正在处理")
                    .font(.title2.weight(.bold))
                    .foregroundStyle(themeMode.textPrimary)
                Spacer()
                Text(activeJobs.isEmpty ? "IDLE" : "LIVE QUEUE")
                    .font(.caption.monospaced().weight(.bold))
                    .tracking(1.8)
                    .foregroundStyle(themeMode.primaryAccent)
            }
            if activeJobs.isEmpty {
                emptyState(icon: "play", title: "等待链接", subtitle: "粘贴公开视频链接后会立即进入队列。")
            } else {
                LazyVStack(spacing: 12) {
                    ForEach(activeJobs) { job in
                        JobSummaryCard(job: job, theme: themeMode, isActionDisabled: store.isLoading || isAnyArtifactOperationActive, onCancel: {
                            Task { await controller.cancelJob(id: job.id, store: store) }
                        }, onDelete: {
                            pendingDeleteJob = job
                        })
                    }
                }
            }
        }
    }

    private var insightCards: some View {
        HStack(spacing: 14) {
            ConsoleInsightCard(systemImage: "checkmark.circle", title: completedJobs.isEmpty ? "完成后保存" : "已完成 \(completedJobs.count) 个", subtitle: store.settings.autoSaveCompletedArtifactsToPhotos ? "完成后自动保存到相册" : "设置里可开启自动保存", theme: themeMode)
            ConsoleInsightCard(systemImage: "music.note", title: preferredDeepLinkMode == .audio ? "MP3 模式已选" : "音频可导出", subtitle: "支持链接转 MP3", theme: themeMode)
        }
    }

    private func emptyState(icon: String, title: String, subtitle: String) -> some View {
        VStack(spacing: 11) {
            Image(systemName: icon)
                .font(.system(size: 32, weight: .semibold))
                .foregroundStyle(themeMode.primaryAccent)
            Text(title)
                .font(.headline.weight(.semibold))
                .foregroundStyle(themeMode.textPrimary)
            Text(subtitle)
                .font(.footnote)
                .multilineTextAlignment(.center)
                .foregroundStyle(themeMode.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 26)
        .consoleInset(theme: themeMode, cornerRadius: 22)
    }

    private func statusRow(systemImage: String, title: String, subtitle: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: systemImage)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(themeMode.secondaryAccent)
                .frame(width: 32, height: 32)
                .background(themeMode.secondaryAccent.opacity(0.14), in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(themeMode.textPrimary)
                Text(subtitle)
                    .font(.footnote)
                    .foregroundStyle(themeMode.textSecondary)
            }
        }
        .padding(12)
        .consoleInset(theme: themeMode)
    }

    @ViewBuilder
    private var selectedContent: some View {
        switch selectedConsoleTab {
        case .tasks:
            tasksContent
        case .history:
            historyContent
        case .settings:
            settingsContent
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 22) {
                    ConsoleHeader(
                        theme: themeMode,
                        selectedTab: selectedConsoleTab,
                        jobCount: store.jobs.count,
                        onSettings: { selectedConsoleTab = .settings },
                        onSelectTheme: { mode in
                            withAnimation(.easeInOut(duration: 0.18)) {
                                themeMode = mode
                            }
                        }
                    )
                    selectedContent
                }
                .padding(.horizontal, 20)
                .padding(.top, 24)
                .padding(.bottom, 116)
            }
            .background(ConsoleBackground(theme: themeMode).ignoresSafeArea())
            .scrollContentBackground(.hidden)
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .safeAreaInset(edge: .bottom) {
                PremiumToolbar(selectedTab: $selectedConsoleTab, theme: themeMode)
                    .padding(.horizontal, 18)
                    .padding(.bottom, 8)
            }
            .onChange(of: store.jobs.map { "\($0.id):\($0.status.rawValue):\($0.updatedAt.timeIntervalSince1970)" }) {
                autoSaveCompletedArtifactsIfNeeded()
            }
            .onChange(of: store.settings.autoSaveCompletedArtifactsToPhotos) {
                autoSaveCompletedArtifactsIfNeeded()
            }
            .onChange(of: selectedConsoleTab) {
                guard selectedConsoleTab == .settings, hasServerSettings else { return }
                Task { await controller.refreshYouTubeCookieStatus(store: store) }
            }
            .navigationDestination(for: Job.ID.self) { jobID in
                if let job = store.job(id: jobID) {
                    JobDetailScreen(
                        job: job,
                        store: store,
                        artifactState: artifactStates[jobID],
                        sharedArtifactURLs: sharedArtifactURLs,
                        activeArtifactAction: activeArtifactAction,
                        photoSaveProgressStates: photoSaveProgressStates,
                        theme: themeMode,
                        onCancel: {
                            Task { await controller.cancelJob(id: job.id, store: store) }
                        },
                        onRetry: {
                            Task { await retryJob(job) }
                        },
                        onDelete: {
                            pendingDeleteJob = job
                        },
                        onRefreshArtifacts: {
                            Task { await refreshArtifacts(for: job, force: true) }
                        },
                        onShareArtifact: { artifact in
                            Task { await prepareArtifactForSharing(artifact) }
                        },
                        onSaveArtifactToPhotos: { artifact in
                            Task { await saveArtifactToPhotos(artifact, jobID: job.id) }
                        }
                    )
                    .task(id: "\(job.id)-\(job.updatedAt.timeIntervalSince1970)") {
                        await refreshArtifacts(for: job)
                    }
                } else {
                    ContentUnavailableView("任务不存在", systemImage: "tray", description: Text("这条记录可能已被删除。"))
                }
            }
            .confirmationDialog(
                "删除这条下载记录和文件？",
                isPresented: Binding(
                    get: { pendingDeleteJob != nil },
                    set: { isPresented in
                        if !isPresented {
                            pendingDeleteJob = nil
                        }
                    }
                ),
                titleVisibility: .visible
            ) {
                Button("删除文件和记录", role: .destructive) {
                    guard !store.isLoading, activeArtifactAction == nil, let pendingDeleteJob else { return }
                    let deletedJobID = pendingDeleteJob.id
                    Task {
                        let deletedArtifacts: [ArtifactSummary]
                        if case let .loaded(artifacts)? = artifactStates[deletedJobID] {
                            deletedArtifacts = artifacts
                        } else {
                            deletedArtifacts = []
                        }
                        await controller.deleteJob(id: deletedJobID, store: store)
                        if !store.jobs.contains(where: { $0.id == deletedJobID }) {
                            artifactStates.removeValue(forKey: deletedJobID)
                            for artifact in deletedArtifacts {
                                if let url = sharedArtifactURLs.removeValue(forKey: artifact.id) {
                                    try? FileManager.default.removeItem(at: url.deletingLastPathComponent())
                                }
                            }
                        }
                    }
                    self.pendingDeleteJob = nil
                }
                Button("取消", role: .cancel) {
                    pendingDeleteJob = nil
                }
            } message: {
                Text("会删除下载文件和这条历史记录，处理中任务不能删除。")
            }
            .overlay(alignment: .bottom) {
                if let savingStatusText {
                    Text(savingStatusText)
                        .font(.footnote)
                        .foregroundStyle(.black.opacity(0.88))
                        .lineLimit(1)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(themeMode.primaryAccent.opacity(0.94), in: Capsule())
                        .padding(.bottom, 88)
                        .padding(.horizontal)
                        .accessibilityAddTraits(.isStaticText)
                } else if let errorMessage = store.errorMessage {
                    Text(errorMessage)
                        .font(.footnote)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(themeMode.danger.opacity(0.94), in: Capsule())
                        .padding(.bottom, 88)
                        .padding(.horizontal)
                        .accessibilityAddTraits(.isStaticText)
                } else if let successMessage {
                    Text(successMessage)
                        .font(.footnote)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(themeMode.success.opacity(0.94), in: Capsule())
                        .padding(.bottom, 88)
                        .padding(.horizontal)
                        .accessibilityAddTraits(.isStaticText)
                }
            }
        }
    }
}
private struct ConsoleHeader: View {
    let theme: ConsoleThemeMode
    let selectedTab: ConsoleTab
    let jobCount: Int
    let onSettings: () -> Void
    let onSelectTheme: (ConsoleThemeMode) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top, spacing: 16) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("XDOWNLOADER")
                        .font(.caption.monospaced().weight(.bold))
                        .tracking(2.2)
                        .foregroundStyle(theme.primaryAccent)
                    Text(selectedTab == .tasks ? "下载控制台" : selectedTab.title)
                        .font(.system(size: 38, weight: .bold, design: .rounded))
                        .foregroundStyle(theme.textPrimary)
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(theme.textSecondary)
                        .lineSpacing(3)
                }
                Spacer(minLength: 10)
                Menu {
                    Button("切换到高级工具风") { onSelectTheme(.premium) }
                    Button("切换到霓虹灯风") { onSelectTheme(.neon) }
                    Button("打开设置", action: onSettings)
                } label: {
                    Image(systemName: "slider.horizontal.3")
                        .font(.system(size: 22, weight: .semibold))
                        .foregroundStyle(theme.textPrimary)
                        .frame(width: 64, height: 64)
                        .background(theme.surfaceElevated.opacity(0.86), in: Circle())
                        .overlay(Circle().stroke(theme.border, lineWidth: 1))
                }
                .accessibilityLabel("主题和设置")
            }
            HStack(spacing: 10) {
                HeaderMetric(title: "\(jobCount) 个任务", systemImage: "tray.full.fill", tint: theme.secondaryAccent, theme: theme)
                HeaderMetric(title: theme.title, systemImage: theme == .premium ? "sparkle" : "bolt.fill", tint: theme.primaryAccent, theme: theme)
            }
        }
    }

    private var subtitle: String {
        switch selectedTab {
        case .tasks: "粘贴链接，解析、下载、保存一气呵成。"
        case .history: "查看已完成、失败和取消的任务记录。"
        case .settings: "管理服务器邀请码、视觉主题和自动保存。"
        }
    }
}

private struct HeaderMetric: View {
    let title: String
    let systemImage: String
    let tint: Color
    let theme: ConsoleThemeMode

    var body: some View {
        Label(title, systemImage: systemImage)
            .font(.caption.weight(.semibold))
            .foregroundStyle(theme.textSecondary)
            .symbolRenderingMode(.hierarchical)
            .tint(tint)
            .padding(.horizontal, 11)
            .padding(.vertical, 8)
            .background(theme.surfaceElevated.opacity(0.92), in: Capsule())
            .overlay(Capsule().stroke(theme.border, lineWidth: 1))
    }
}

private struct DashboardCard<Content: View>: View {
    let theme: ConsoleThemeMode
    let content: Content

    init(theme: ConsoleThemeMode = .premium, @ViewBuilder content: () -> Content) {
        self.theme = theme
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .consoleCard(theme: theme)
    }
}

private struct SectionTitle: View {
    let title: String
    let subtitle: String
    let theme: ConsoleThemeMode

    init(title: String, subtitle: String, theme: ConsoleThemeMode = .premium) {
        self.title = title
        self.subtitle = subtitle
        self.theme = theme
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title)
                .font(.headline.weight(.semibold))
                .foregroundStyle(theme.textPrimary)
            Text(subtitle)
                .font(.footnote)
                .foregroundStyle(theme.textSecondary)
                .lineSpacing(2)
        }
    }
}

private struct ConsoleMetricCard: View {
    let title: String
    let value: String
    var highlight = false
    let theme: ConsoleThemeMode

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(theme.textSecondary)
            Text(value)
                .font(.system(size: value.count > 5 ? 24 : 31, weight: .bold, design: .rounded))
                .minimumScaleFactor(0.62)
                .lineLimit(1)
                .foregroundStyle(highlight ? theme.secondaryAccent : theme.textPrimary)
        }
        .frame(maxWidth: .infinity, minHeight: 78, alignment: .leading)
        .padding(16)
        .background(theme.surfaceInset.opacity(0.96), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(theme.border, lineWidth: 1))
    }
}

private struct ConsoleInsightCard: View {
    let systemImage: String
    let title: String
    let subtitle: String
    let theme: ConsoleThemeMode

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Image(systemName: systemImage)
                .font(.title3.weight(.semibold))
                .foregroundStyle(theme.secondaryAccent)
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(theme.textPrimary)
            Text(subtitle)
                .font(.footnote)
                .foregroundStyle(theme.textSecondary)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, minHeight: 110, alignment: .leading)
        .padding(16)
        .background(theme.surfaceElevated.opacity(0.78), in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).stroke(theme.border, lineWidth: 1))
    }
}

private struct PremiumToolbar: View {
    @Binding var selectedTab: ConsoleTab
    let theme: ConsoleThemeMode

    var body: some View {
        HStack(spacing: 8) {
            ForEach(ConsoleTab.allCases) { tab in
                Button {
                    withAnimation(.easeInOut(duration: 0.16)) {
                        selectedTab = tab
                    }
                } label: {
                    VStack(spacing: 4) {
                        Image(systemName: tab.systemImage)
                            .font(.system(size: 20, weight: .semibold))
                        Text(tab.title)
                            .font(.caption2.weight(.semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .foregroundStyle(selectedTab == tab ? Color.black.opacity(0.9) : theme.textSecondary)
                    .background(selectedTab == tab ? theme.primaryAccent : Color.clear, in: Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(tab.title)
                .accessibilityValue(selectedTab == tab ? "当前选中" : "")
                .accessibilityAddTraits(selectedTab == tab ? .isSelected : [])
            }
        }
        .padding(8)
        .background(.black.opacity(0.86), in: Capsule())
        .overlay(Capsule().stroke(theme.border, lineWidth: 1))
        .shadow(color: .black.opacity(0.32), radius: 16, x: 0, y: 10)
    }
}

private struct JobSummaryCard: View {
    let job: Job
    let theme: ConsoleThemeMode
    let isActionDisabled: Bool
    let onCancel: () -> Void
    let onDelete: () -> Void

    init(job: Job, theme: ConsoleThemeMode = .premium, isActionDisabled: Bool = false, onCancel: @escaping () -> Void, onDelete: @escaping () -> Void) {
        self.job = job
        self.theme = theme
        self.isActionDisabled = isActionDisabled
        self.onCancel = onCancel
        self.onDelete = onDelete
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            NavigationLink(value: job.id) {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: job.status.isTerminal ? "checkmark.circle" : "play")
                            .font(.system(size: 22, weight: .semibold))
                            .foregroundStyle(theme.primaryAccent)
                            .frame(width: 42, height: 42)
                            .background(theme.primaryAccent.opacity(0.10), in: Circle())
                        VStack(alignment: .leading, spacing: 5) {
                            Text(job.mediaTitle ?? job.sourceURL)
                                .font(.headline.weight(.semibold))
                                .foregroundStyle(theme.textPrimary)
                                .lineLimit(2)
                            Text(job.secondaryStatusText)
                                .font(.subheadline)
                                .foregroundStyle(theme.textSecondary)
                                .lineLimit(2)
                        }
                        Spacer(minLength: 8)
                        TaskStatusBadge(status: job.status)
                    }
                    DownloadProgressDetails(job: job)
                    HStack(spacing: 8) {
                        Label(job.status.isTerminal ? "打开详情" : "查看进度", systemImage: "folder")
                            .frame(maxWidth: .infinity)
                        Label(job.status.isTerminal ? "管理文件" : "队列中", systemImage: "doc.on.doc")
                            .frame(maxWidth: .infinity)
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(theme.textSecondary)
                    .padding(.top, 2)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)
            Menu {
                if job.status.isTerminal {
                    Button("删除文件和记录", role: .destructive, action: onDelete)
                } else {
                    Button("取消任务", role: .destructive, action: onCancel)
                }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.headline.weight(.semibold))
                    .foregroundStyle(theme.textSecondary)
                    .frame(width: 34, height: 34)
                    .background(theme.surface, in: Circle())
                    .overlay(Circle().stroke(theme.border, lineWidth: 1))
            }
            .accessibilityLabel("更多操作")
            .disabled(isActionDisabled)
        }
        .padding(18)
        .background(theme.surfaceInset.opacity(0.96), in: RoundedRectangle(cornerRadius: 28, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 28, style: .continuous).stroke(theme.border, lineWidth: 1))
    }
}


private struct JobDetailScreen: View {
    let job: Job
    @Bindable var store: JobStore
    let artifactState: ArtifactLoadState?
    let sharedArtifactURLs: [ArtifactSummary.ID: URL]
    let activeArtifactAction: ArtifactActionState?
    let photoSaveProgressStates: [ArtifactSummary.ID: PhotoSaveProgressState]
    let theme: ConsoleThemeMode
    private var themeMode: ConsoleThemeMode { theme }
    let onCancel: () -> Void
    let onRetry: () -> Void
    let onDelete: () -> Void
    let onRefreshArtifacts: () -> Void
    let onShareArtifact: (ArtifactSummary) -> Void
    let onSaveArtifactToPhotos: (ArtifactSummary) -> Void

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(alignment: .top, spacing: 12) {
                        Text(job.mediaTitle ?? "未命名任务")
                            .font(.headline.weight(.semibold))
                            .foregroundStyle(theme.textPrimary)
                            .lineLimit(3)
                        Spacer(minLength: 8)
                        TaskStatusBadge(status: job.status)
                    }
                    DownloadProgressDetails(job: job)
                    if let authorHandle = job.authorHandle {
                        detailPill(title: "作者", value: authorHandle)
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        Text("来源")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(theme.textMuted)
                            .textCase(.uppercase)
                        Text(job.sourceURL)
                            .font(.footnote.monospaced())
                            .foregroundStyle(themeMode.textSecondary)
                            .textSelection(.enabled)
                            .lineLimit(3)
                    }
                    .padding(12)
                    .consoleInset(theme: theme)
                    if let selectedQuality = job.selectedQuality {
                        detailPill(title: "清晰度", value: selectedQuality)
                    }
                }
                .padding(.vertical, 4)
            } header: {
                Text("任务状态")
                    .foregroundStyle(theme.textMuted)
            }
            .listRowBackground(Color.clear)
            if job.status == .completed, (job.jobType == .audioSeparation || job.artifactID != nil) {
                ArtifactListSection(
                    job: job,
                    state: artifactState,
                    sharedArtifactURLs: sharedArtifactURLs,
                    activeArtifactAction: activeArtifactAction,
                    photoSaveProgressStates: photoSaveProgressStates,
                    theme: theme,
                    onRefresh: onRefreshArtifacts,
                    onShare: onShareArtifact,
                    onSaveToPhotos: onSaveArtifactToPhotos
                )
            }
            Section {
                if !job.status.isTerminal {
                    Button("取消任务", role: .destructive, action: onCancel)
                        .disabled(store.isLoading)
                }
                if job.status == .failed || job.status == .canceled {
                    Button("重试", action: onRetry)
                        .disabled(store.isLoading)
                }
                if job.status.isTerminal {
                    Button("删除文件和记录", role: .destructive, action: onDelete)
                        .disabled(store.isLoading || activeArtifactAction != nil)
                }
            } header: {
                Text("操作")
                    .foregroundStyle(theme.textMuted)
            }
            .listRowBackground(theme.surface.opacity(0.94))
        }
        .scrollContentBackground(.hidden)
        .background(ConsoleBackground(theme: theme).ignoresSafeArea())
        .navigationTitle("任务详情")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func detailPill(title: String, value: String) -> some View {
        HStack {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(theme.textMuted)
            Spacer()
            Text(value)
                .font(.caption.monospaced())
                .foregroundStyle(themeMode.textPrimary)
        }
        .padding(12)
        .consoleInset(theme: theme)
    }
}

private struct ArtifactListSection: View {
    let job: Job
    let state: ArtifactLoadState?
    let sharedArtifactURLs: [ArtifactSummary.ID: URL]
    let activeArtifactAction: ArtifactActionState?
    let photoSaveProgressStates: [ArtifactSummary.ID: PhotoSaveProgressState]
    let theme: ConsoleThemeMode
    private var themeMode: ConsoleThemeMode { theme }
    let onRefresh: () -> Void
    let onShare: (ArtifactSummary) -> Void
    let onSaveToPhotos: (ArtifactSummary) -> Void

    var body: some View {
        Section {
            switch state ?? .loading {
            case .loading:
                HStack(spacing: 8) {
                    ProgressView()
                        .tint(theme.primaryAccent)
                    Text("正在读取文件详情…")
                        .foregroundStyle(themeMode.textSecondary)
                }
            case let .failed(message):
                Text(message)
                    .foregroundStyle(theme.danger)
                Button("重新读取", action: onRefresh)
                    .buttonStyle(ConsoleSecondaryButtonStyle(theme: themeMode))
            case let .loaded(artifacts):
                if artifacts.isEmpty {
                    Text("暂无可分享文件。")
                        .foregroundStyle(themeMode.textSecondary)
                    Button("重新读取", action: onRefresh)
                        .buttonStyle(ConsoleSecondaryButtonStyle(theme: themeMode))
                } else {
                    ForEach(artifacts) { artifact in
                        ArtifactRow(
                            artifact: artifact,
                            shareURL: sharedArtifactURLs[artifact.id],
                            isPreparingShare: activeArtifactAction == .preparingShare(artifact.id),
                            isSavingToPhotos: activeArtifactAction == .savingToPhotos(artifact.id),
                            photoSaveProgressState: photoSaveProgressStates[artifact.id],
                            isDisabled: activeArtifactAction != nil,
                            theme: theme,
                            onPrepare: { onShare(artifact) },
                            onSaveToPhotos: { onSaveToPhotos(artifact) }
                        )
                    }
                }
            }
        } header: {
            Text(job.jobType == .audioSeparation ? "分离结果" : "下载文件")
                .foregroundStyle(theme.textMuted)
        }
        .listRowBackground(Color.clear)
    }
}

private struct ArtifactRow: View {
    let artifact: ArtifactSummary
    let shareURL: URL?
    let isPreparingShare: Bool
    let isSavingToPhotos: Bool
    let photoSaveProgressState: PhotoSaveProgressState?
    let isDisabled: Bool
    let theme: ConsoleThemeMode
    private var themeMode: ConsoleThemeMode { theme }
    let onPrepare: () -> Void
    let onSaveToPhotos: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: artifact.role == .media ? "film.stack" : "waveform")
                    .foregroundStyle(themeMode.primaryAccent)
                    .frame(width: 34, height: 34)
                    .background(theme.primaryAccent.opacity(0.12), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                VStack(alignment: .leading, spacing: 4) {
                    Text(artifactTitle(artifact.role))
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(themeMode.textPrimary)
                    Text(artifact.fileName)
                        .font(.caption.monospaced())
                        .foregroundStyle(theme.textMuted)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
            if let mediaDetailsText = artifact.mediaDetailsText {
                Text(mediaDetailsText)
                    .font(.caption2.monospaced())
                    .foregroundStyle(themeMode.textSecondary)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .background(theme.surface, in: Capsule())
            }
            if canSaveArtifactToPhotos(artifact) {
                Button {
                    onSaveToPhotos()
                } label: {
                    HStack(spacing: 8) {
                        if isSavingToPhotos {
                            ProgressView()
                                .tint(.black.opacity(0.8))
                        }
                        Text(photoSaveButtonTitle)
                    }
                }
                .buttonStyle(ConsolePrimaryButtonStyle(theme: themeMode))
                .disabled(isDisabled)
                if let photoSaveProgressState {
                    photoSaveProgressView(photoSaveProgressState)
                }
            }
            shareControl
            if isDisabled, !isPreparingShare, !isSavingToPhotos {
                Text("等待当前文件准备完成。")
                    .font(.caption2)
                    .foregroundStyle(theme.textMuted)
            }
        }
        .padding(14)
        .background(theme.surfaceInset.opacity(0.96), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(theme.border, lineWidth: 1)
        )
    }

    @ViewBuilder
    private var shareControl: some View {
        if let shareURL, FileManager.default.fileExists(atPath: shareURL.path) {
            ShareLink(item: shareURL) {
                Label("分享文件", systemImage: "square.and.arrow.up")
            }
            .buttonStyle(ConsoleSecondaryButtonStyle(theme: themeMode))
            .disabled(isDisabled)
        } else if canSaveArtifactToPhotos(artifact) {
            prepareShareButton.buttonStyle(ConsoleSecondaryButtonStyle(theme: themeMode))
        } else {
            prepareShareButton.buttonStyle(ConsolePrimaryButtonStyle(theme: themeMode))
        }
    }

    @ViewBuilder
    private func photoSaveProgressView(_ state: PhotoSaveProgressState) -> some View {
        switch state {
        case let .downloading(progress):
            VStack(alignment: .leading, spacing: 4) {
                if let fraction = progress.fraction {
                    ProgressView(value: fraction)
                        .tint(theme.primaryAccent)
                } else {
                    ProgressView()
                        .tint(theme.primaryAccent)
                }
                if let details = photoSaveProgressDetails(progress) {
                    Text(details)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(themeMode.textSecondary)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(photoSaveAccessibilityText)
        case .savingToPhotos:
            progressStatusText("正在写入系统相册…")
        case .cleaningServer:
            progressStatusText("正在清理服务器文件…")
        }
    }

    private func progressStatusText(_ text: String) -> some View {
        HStack(spacing: 6) {
            ProgressView()
                .tint(theme.primaryAccent)
            Text(text)
                .font(.caption2)
                .foregroundStyle(themeMode.textSecondary)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text)
    }

    private var photoSaveButtonTitle: String {
        guard let photoSaveProgressState else {
            return "保存到相册"
        }
        switch photoSaveProgressState {
        case let .downloading(progress):
            if let fraction = progress.fraction {
                return "正在传输到 iPhone \(Int((fraction * 100).rounded()))%"
            }
            return "正在传输到 iPhone"
        case .savingToPhotos:
            return "正在写入相册…"
        case .cleaningServer:
            return "正在清理服务器文件…"
        }
    }

    private var photoSaveAccessibilityText: String {
        guard let photoSaveProgressState else {
            return "保存到相册"
        }
        switch photoSaveProgressState {
        case let .downloading(progress):
            return [photoSaveButtonTitle, photoSaveProgressDetails(progress)].compactMap { $0 }.joined(separator: "，")
        case .savingToPhotos, .cleaningServer:
            return photoSaveButtonTitle
        }
    }

    private func photoSaveProgressDetails(_ progress: ArtifactDownloadProgress) -> String? {
        var items: [String] = []
        let receivedText = Self.byteCountFormatter.string(fromByteCount: progress.receivedBytes)
        if let totalBytes = progress.totalBytes {
            let totalText = Self.byteCountFormatter.string(fromByteCount: totalBytes)
            items.append("已下载 \(receivedText) / \(totalText)")
        } else {
            items.append("已下载 \(receivedText)")
        }
        if let bytesPerSecond = progress.bytesPerSecond, bytesPerSecond > 0 {
            items.append("\(Self.byteCountFormatter.string(fromByteCount: Int64(bytesPerSecond)))/s")
        }
        if let etaSeconds = progress.etaSeconds, etaSeconds > 1 {
            items.append("约 \(Self.durationFormatter.string(from: etaSeconds) ?? "几秒")")
        }
        return items.isEmpty ? nil : items.joined(separator: " · ")
    }

    private static let byteCountFormatter: ByteCountFormatter = {
        let formatter = ByteCountFormatter()
        formatter.allowedUnits = [.useKB, .useMB, .useGB]
        formatter.countStyle = .file
        formatter.includesUnit = true
        formatter.isAdaptive = true
        return formatter
    }()

    private static let durationFormatter: DateComponentsFormatter = {
        let formatter = DateComponentsFormatter()
        formatter.allowedUnits = [.hour, .minute, .second]
        formatter.maximumUnitCount = 2
        formatter.unitsStyle = .full
        return formatter
    }()

    private var prepareShareButton: some View {
        Button {
            onPrepare()
        } label: {
            HStack(spacing: 8) {
                if isPreparingShare {
                    ProgressView()
                }
                Text(isPreparingShare ? "正在准备…" : "准备分享")
            }
        }
        .disabled(isDisabled)
    }

    private func artifactTitle(_ role: ArtifactRole) -> String {
        switch role {
        case .media:
            "媒体文件"
        case .vocals:
            "人声"
        case .accompaniment:
            "伴奏"
        }
    }
}
