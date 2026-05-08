import Foundation
import Observation

private let audioUploadMaxBytes = 200 * 1024 * 1024
private let youtubeCookieUploadMaxBytes = 2 * 1024 * 1024
private let allowedAudioExtensions = Set(["mp3", "wav", "m4a", "aac", "flac"])
private let batchSubmissionMaxCharacters = 64 * 1024
private let batchSubmissionMaxURLs = 20

private struct ValidationError: LocalizedError {
    let errorDescription: String?

    init(_ errorDescription: String) {
        self.errorDescription = errorDescription
    }
}

public protocol ClientAPI: Sendable {
    func registerDevice(name: String, platform: String, appVersion: String, bootstrapCode: String?) async throws -> DeviceRegistration
    func previewJob(url: String, jobType: JobType, token: String) async throws -> JobPreview
    func createJob(url: String, preferredQuality: String?, token: String) async throws -> Job
    func createAudioDownloadJob(url: String, token: String) async throws -> Job
    func createAudioSeparationJob(fileURL: URL, token: String) async throws -> Job
    func youtubeCookieStatus(token: String) async throws -> YouTubeCookieStatus
    func uploadYouTubeCookies(fileURL: URL, token: String) async throws -> YouTubeCookieStatus
    func deleteYouTubeCookies(token: String) async throws -> YouTubeCookieStatus
    func cancelJob(id: String, token: String) async throws -> Job
    func retryJob(id: String, token: String) async throws -> Job
    func listJobs(token: String) async throws -> [Job]
    func listJobArtifacts(jobID: String, token: String) async throws -> [ArtifactSummary]
    func listJobLogs(jobID: String, token: String, limit: Int, afterID: Int?) async throws -> JobLogsResult
    func deleteArtifact(id: String, token: String) async throws
    func deleteHistory(token: String) async throws -> DeleteHistoryResult
    func deleteJob(id: String, token: String) async throws -> Job
    func downloadArtifact(id: String, token: String) async throws -> DownloadedArtifact
    func downloadArtifact(
        id: String,
        token: String,
        onProgress: @Sendable @escaping (ArtifactDownloadProgress) async -> Void
    ) async throws -> DownloadedArtifact
}

public extension ClientAPI {
    func downloadArtifact(
        id: String,
        token: String,
        onProgress: @Sendable @escaping (ArtifactDownloadProgress) async -> Void
    ) async throws -> DownloadedArtifact {
        try await downloadArtifact(id: id, token: token)
    }
}

public protocol RegistrationStore: Sendable {
    func loadRegistration() async throws -> DeviceRegistration?
    func saveRegistration(_ registration: DeviceRegistration) async throws
}

public protocol JobsStore: Sendable {
    func loadJobs() async throws -> [Job]
    func saveJobs(_ jobs: [Job]) async throws
}

@MainActor
public final class AppController {
    private let apiClient: ClientAPI
    private let registrationStore: RegistrationStore
    private let jobsStore: JobsStore
    private let deviceName: String
    private let platform: String
    private let appVersion: String
    private var pollingTask: Task<Void, Never>?

    public init(
        apiClient: ClientAPI,
        registrationStore: RegistrationStore,
        jobsStore: JobsStore,
        deviceName: String,
        platform: String,
        appVersion: String
    ) {
        self.apiClient = apiClient
        self.registrationStore = registrationStore
        self.jobsStore = jobsStore
        self.deviceName = deviceName
        self.platform = platform
        self.appVersion = appVersion
    }

    deinit {
        pollingTask?.cancel()
    }

    public func bootstrap(store: JobStore) async {
        do {
            let cachedJobs = try await jobsStore.loadJobs()
            store.replaceJobs(cachedJobs)
        } catch {
            store.setError(error.localizedDescription)
        }
    }

    public func start(store: JobStore) async {
        await bootstrap(store: store)
        do {
            try await ensureRegistrationIfPossible(store: store)
            await refreshJobs(store: store)
            if store.hasActiveJobs {
                startPolling(store: store)
            }
        } catch {
            store.setError(error.localizedDescription)
        }
    }

    public func ensureRegistrationIfPossible(store: JobStore) async throws {
        if store.registration != nil {
            return
        }
        if let cachedRegistration = try await registrationStore.loadRegistration() {
            store.setRegistration(cachedRegistration)
        }
    }

    public func ensureRegistration(store: JobStore) async throws {
        if let registration = store.registration {
            return store.setRegistration(registration)
        }
        if let cachedRegistration = try await registrationStore.loadRegistration() {
            store.setRegistration(cachedRegistration)
            return
        }
        if store.settings.apiBaseURL.host != "127.0.0.1", store.settings.bootstrapCode?.isEmpty != false {
            throw ValidationError("请输入服务器邀请码。")
        }
        let registration = try await apiClient.registerDevice(
            name: deviceName,
            platform: platform,
            appVersion: appVersion,
            bootstrapCode: store.settings.bootstrapCode
        )
        try await registrationStore.saveRegistration(registration)
        store.setRegistration(registration)
    }

    public func refreshJobs(store: JobStore) async {
        guard let token = store.registration?.accessToken else {
            return
        }
        guard !store.isLoading else {
            return
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            let jobs = try await apiClient.listJobs(token: token)
            store.replaceJobsPreservingActiveLocalJobs(jobs)
            try await jobsStore.saveJobs(store.jobs)
            store.setError(nil)
        } catch {
            store.setError(error.localizedDescription)
        }
    }

    public func previewCurrentURL(store: JobStore, jobType: JobType = .download) async -> JobPreview? {
        guard !store.isLoading else {
            return nil
        }
        let rawURL = store.draftURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !rawURL.isEmpty else {
            store.setError("请先粘贴分享链接。")
            return nil
        }
        guard let url = Self.firstSupportedSourceURL(in: rawURL) else {
            store.setError("请粘贴有效的 X、抖音、皮皮虾、小红书、Bilibili 或 YouTube 公开链接。")
            return nil
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            if store.registration?.accessToken == nil {
                try await ensureRegistration(store: store)
            }
            guard let token = store.registration?.accessToken else {
                store.setError("设备初始化失败，请重试。")
                return nil
            }
            let preview = try await apiClient.previewJob(url: url, jobType: jobType, token: token)
            store.setError(nil)
            return preview
        } catch {
            store.setError(error.localizedDescription)
            return nil
        }
    }

    public func submitCurrentURL(store: JobStore) async -> Job? {
        guard !store.isLoading else {
            return nil
        }
        let rawURL = store.draftURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !rawURL.isEmpty else {
            store.setError("请先粘贴分享链接。")
            return nil
        }
        guard let url = Self.firstSupportedSourceURL(in: rawURL) else {
            store.setError("请粘贴有效的 X、抖音、皮皮虾、小红书、Bilibili 或 YouTube 公开链接。")
            return nil
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            if store.registration?.accessToken == nil {
                try await ensureRegistration(store: store)
            }
            guard let token = store.registration?.accessToken else {
                store.setError("设备初始化失败，请重试。")
                return nil
            }
            let job = try await apiClient.createJob(
                url: url,
                preferredQuality: store.settings.preferredQuality,
                token: token
            )
            store.upsert(job)
            try await jobsStore.saveJobs(store.jobs)
            store.clearDraftURL()
            store.setError(nil)
            startPolling(store: store)
            return job
        } catch {
            store.setError(error.localizedDescription)
            return nil
        }
    }

    public func submitAudioDownloadURL(store: JobStore) async -> Job? {
        guard !store.isLoading else {
            return nil
        }
        let rawURL = store.draftURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !rawURL.isEmpty else {
            store.setError("请先粘贴分享链接。")
            return nil
        }
        guard let url = Self.firstSupportedSourceURL(in: rawURL) else {
            store.setError("请粘贴有效的 X、抖音、皮皮虾、小红书、Bilibili 或 YouTube 公开链接。")
            return nil
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            if store.registration?.accessToken == nil {
                try await ensureRegistration(store: store)
            }
            guard let token = store.registration?.accessToken else {
                store.setError("设备初始化失败，请重试。")
                return nil
            }
            let job = try await apiClient.createAudioDownloadJob(url: url, token: token)
            store.upsert(job)
            try await jobsStore.saveJobs(store.jobs)
            store.clearDraftURL()
            store.setError(nil)
            startPolling(store: store)
            return job
        } catch {
            store.setError(error.localizedDescription)
            return nil
        }
    }

    public func refreshYouTubeCookieStatus(store: JobStore) async -> YouTubeCookieStatus? {
        guard !store.isLoading else { return nil }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            if store.registration?.accessToken == nil {
                try await ensureRegistration(store: store)
            }
            guard let token = store.registration?.accessToken else {
                store.setError("设备初始化失败，请重试。")
                return nil
            }
            let status = try await apiClient.youtubeCookieStatus(token: token)
            store.setYouTubeCookieStatus(status)
            store.setError(nil)
            return status
        } catch {
            store.setError(error.localizedDescription)
            return nil
        }
    }

    public func uploadYouTubeCookies(fileURL: URL, store: JobStore) async -> YouTubeCookieStatus? {
        guard !store.isLoading else { return nil }
        guard Self.isSecureCloudCookieBaseURL(store.settings.apiBaseURL) else {
            store.setError("为保护登录 Cookie，云端上传必须使用 HTTPS。")
            return nil
        }
        do {
            try Self.validateYouTubeCookieFile(fileURL)
        } catch {
            store.setError(error.localizedDescription)
            return nil
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            if store.registration?.accessToken == nil {
                try await ensureRegistration(store: store)
            }
            guard let token = store.registration?.accessToken else {
                store.setError("设备初始化失败，请重试。")
                return nil
            }
            let status = try await apiClient.uploadYouTubeCookies(fileURL: fileURL, token: token)
            store.setYouTubeCookieStatus(status)
            store.setError(nil)
            return status
        } catch {
            store.setError(error.localizedDescription)
            return nil
        }
    }

    public func deleteYouTubeCookies(store: JobStore) async -> YouTubeCookieStatus? {
        guard !store.isLoading else { return nil }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            if store.registration?.accessToken == nil {
                try await ensureRegistration(store: store)
            }
            guard let token = store.registration?.accessToken else {
                store.setError("设备初始化失败，请重试。")
                return nil
            }
            let status = try await apiClient.deleteYouTubeCookies(token: token)
            store.setYouTubeCookieStatus(status)
            store.setError(nil)
            return status
        } catch {
            store.setError(error.localizedDescription)
            return nil
        }
    }

    public func submitBatchURLs(store: JobStore) async -> BatchSubmissionResult {
        guard !store.isLoading else {
            return BatchSubmissionResult(requestedCount: 0, succeededCount: 0, failedCount: 0, jobs: [])
        }
        guard store.batchDraftText.count <= batchSubmissionMaxCharacters else {
            store.setError("批量文本过长，请减少后重试。")
            return BatchSubmissionResult(requestedCount: 0, succeededCount: 0, failedCount: 0, jobs: [])
        }
        let extraction = ClipboardURLExtractor.supportedURLs(in: store.batchDraftText, maxURLs: batchSubmissionMaxURLs)
        let urls = extraction.urls
        guard !urls.isEmpty else {
            store.setError("请粘贴至少一个有效的分享链接。")
            return BatchSubmissionResult(requestedCount: 0, succeededCount: 0, failedCount: 0, jobs: [])
        }
        guard !extraction.exceededLimit else {
            store.setError("单次最多支持 20 个链接。")
            return BatchSubmissionResult(requestedCount: batchSubmissionMaxURLs + 1, succeededCount: 0, failedCount: 0, jobs: [])
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            if store.registration?.accessToken == nil {
                try await ensureRegistration(store: store)
            }
            guard let token = store.registration?.accessToken else {
                store.setError("设备初始化失败，请重试。")
                return BatchSubmissionResult(requestedCount: urls.count, succeededCount: 0, failedCount: urls.count, jobs: [])
            }
            var jobs: [Job] = []
            var failedURLs: [String] = []
            var lastError: Error?
            for url in urls {
                do {
                    let job = try await apiClient.createJob(
                        url: url,
                        preferredQuality: store.settings.preferredQuality,
                        token: token
                    )
                    store.upsert(job)
                    jobs.append(job)
                } catch {
                    failedURLs.append(url)
                    lastError = error
                }
            }
            if !jobs.isEmpty {
                try await jobsStore.saveJobs(store.jobs)
                startPolling(store: store)
            }
            if failedURLs.isEmpty {
                store.clearBatchDraftText()
                store.setError(nil)
            } else if !jobs.isEmpty {
                store.batchDraftText = failedURLs.joined(separator: "\n")
                store.setError("已创建 \(jobs.count) 个任务，\(failedURLs.count) 个链接失败，请检查后重试。")
            } else {
                store.setError(lastError?.localizedDescription ?? "批量创建失败，请稍后重试。")
            }
            return BatchSubmissionResult(
                requestedCount: urls.count,
                succeededCount: jobs.count,
                failedCount: failedURLs.count,
                jobs: jobs
            )
        } catch {
            store.setError(error.localizedDescription)
            return BatchSubmissionResult(requestedCount: urls.count, succeededCount: 0, failedCount: urls.count, jobs: [])
        }
    }

    public static func applyClipboardText(_ text: String, to store: JobStore) -> Bool {
        guard store.settings.autoPasteEnabled else {
            return false
        }
        guard store.lastAppliedClipboardText != text else {
            return false
        }
        guard store.draftURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return false
        }
        guard let url = ClipboardURLExtractor.firstSupportedURL(in: text) else {
            store.lastAppliedClipboardText = text
            return false
        }
        store.draftURL = url
        store.lastAppliedClipboardText = text
        store.setError(nil)
        return true
    }

    public func handleDeepLink(_ url: URL, store: JobStore) -> DeepLinkSubmissionMode? {
        guard let action = DeepLinkParser.parse(url) else {
            store.setError("链接无法识别。")
            return nil
        }
        switch action {
        case let .download(sourceURL):
            store.draftURL = sourceURL
            store.setError(nil)
            return .download
        case let .audio(sourceURL):
            store.draftURL = sourceURL
            store.setError(nil)
            return .audio
        }
    }

    public func submitAudioSeparation(fileURL: URL, store: JobStore) async -> Job? {
        guard !store.isLoading else {
            return nil
        }
        do {
            try Self.validateAudioFile(fileURL)
        } catch {
            store.setError(error.localizedDescription)
            return nil
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            if store.registration?.accessToken == nil {
                try await ensureRegistration(store: store)
            }
            guard let token = store.registration?.accessToken else {
                store.setError("设备初始化失败，请重试。")
                return nil
            }
            let job = try await apiClient.createAudioSeparationJob(fileURL: fileURL, token: token)
            store.upsert(job)
            try await jobsStore.saveJobs(store.jobs)
            store.setError(nil)
            startPolling(store: store)
            return job
        } catch {
            store.setError(error.localizedDescription)
            return nil
        }
    }

    public func startPolling(store: JobStore) {
        guard pollingTask == nil else {
            return
        }
        store.setPolling(true)
        let task = Task { [weak self, weak store] in
            guard let self, let store else { return }
            defer {
                Task { @MainActor [weak self, weak store] in
                    guard let self, let store, self.pollingTask?.isCancelled != false else { return }
                    self.pollingTask = nil
                    store.setPolling(false)
                }
            }
            while !Task.isCancelled {
                await self.refreshJobs(store: store)
                if !store.hasActiveJobs {
                    self.stopPolling(store: store)
                    return
                }
                try? await Task.sleep(for: .seconds(2))
            }
        }
        pollingTask = task
    }

    public func stopPolling(store: JobStore) {
        pollingTask?.cancel()
        pollingTask = nil
        store.setPolling(false)
    }

    public func listJobArtifacts(jobID: String, token: String) async throws -> [ArtifactSummary] {
        try await apiClient.listJobArtifacts(jobID: jobID, token: token)
    }

    public func listJobLogs(jobID: String, token: String, limit: Int = 200, afterID: Int? = nil) async throws -> JobLogsResult {
        try await apiClient.listJobLogs(jobID: jobID, token: token, limit: limit, afterID: afterID)
    }

    public func downloadArtifact(id: String, token: String) async throws -> DownloadedArtifact {
        try await apiClient.downloadArtifact(id: id, token: token)
    }

    public func downloadArtifact(
        id: String,
        token: String,
        onProgress: @Sendable @escaping (ArtifactDownloadProgress) async -> Void
    ) async throws -> DownloadedArtifact {
        try await apiClient.downloadArtifact(id: id, token: token, onProgress: onProgress)
    }

    public func cancelJob(id: String, store: JobStore) async {
        guard let token = store.registration?.accessToken else {
            store.setError("设备初始化失败，请重试。")
            return
        }
        guard !store.isLoading else {
            return
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            let job = try await apiClient.cancelJob(id: id, token: token)
            store.upsert(job)
            try await jobsStore.saveJobs(store.jobs)
            store.setError(nil)
        } catch {
            store.setError(error.localizedDescription)
        }
    }

    public func retryJob(id: String, store: JobStore) async {
        guard let token = store.registration?.accessToken else {
            store.setError("设备初始化失败，请重试。")
            return
        }
        guard !store.isLoading else {
            return
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            let job = try await apiClient.retryJob(id: id, token: token)
            store.upsert(job)
            try await jobsStore.saveJobs(store.jobs)
            store.setError(nil)
            startPolling(store: store)
        } catch {
            store.setError(error.localizedDescription)
        }
    }

    public func deleteArtifact(id: String, store: JobStore) async -> Bool {
        guard let token = store.registration?.accessToken else {
            store.setError("设备初始化失败，请重试。")
            return false
        }
        guard !store.isLoading else {
            return false
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            try await apiClient.deleteArtifact(id: id, token: token)
            store.setError(nil)
            return true
        } catch {
            store.setError(error.localizedDescription)
            return false
        }
    }

    public func deleteHistory(store: JobStore) async -> DeleteHistoryResult? {
        guard let token = store.registration?.accessToken else {
            store.setError("设备初始化失败，请重试。")
            return nil
        }
        guard !store.isLoading else {
            return nil
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            let result = try await apiClient.deleteHistory(token: token)
            let deletedJobIDs = Set(result.deletedJobIDs)
            store.replaceJobs(store.jobs.filter { !deletedJobIDs.contains($0.id) })
            try await jobsStore.saveJobs(store.jobs)
            store.setError(nil)
            return result
        } catch {
            store.setError(error.localizedDescription)
            return nil
        }
    }

    public func deleteJob(id: String, store: JobStore) async {
        guard let token = store.registration?.accessToken else {
            store.setError("设备初始化失败，请重试。")
            return
        }
        guard !store.isLoading else {
            return
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            _ = try await apiClient.deleteJob(id: id, token: token)
            store.remove(jobID: id)
            try await jobsStore.saveJobs(store.jobs)
            store.setError(nil)
        } catch {
            store.setError(error.localizedDescription)
        }
    }

    nonisolated public static func validateAudioFile(_ fileURL: URL) throws {
        let resourceValues = try fileURL.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
        guard resourceValues.isRegularFile == true else {
            throw ValidationError("请选择有效的音频文件。")
        }
        guard allowedAudioExtensions.contains(fileURL.pathExtension.lowercased()) else {
            throw ValidationError("请选择 mp3、wav、m4a、aac 或 flac 音频文件。")
        }
        guard let fileSize = resourceValues.fileSize, fileSize <= audioUploadMaxBytes else {
            throw ValidationError("音频文件不能超过 200MB。")
        }
    }

    nonisolated public static func validateYouTubeCookieFile(_ fileURL: URL) throws {
        try validateCookieFile(fileURL)
    }

    nonisolated public static func validateCookieFile(_ fileURL: URL) throws {
        let resourceValues = try fileURL.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
        guard resourceValues.isRegularFile == true else {
            throw ValidationError("请选择有效的 cookies.txt 文件。")
        }
        guard fileURL.pathExtension.lowercased() == "txt" else {
            throw ValidationError("请选择 cookies.txt 文件。")
        }
        guard let fileSize = resourceValues.fileSize, fileSize > 0, fileSize <= youtubeCookieUploadMaxBytes else {
            throw ValidationError("Cookie 文件需为 2MB 以内。")
        }
    }

    nonisolated public static func isSecureCloudCookieBaseURL(_ url: URL) -> Bool {
        let host = url.host?.lowercased()
        return url.scheme == "https" || host == "127.0.0.1" || host == "localhost" || host == "::1" || host == "[::1]"
    }

    private nonisolated static func firstSupportedSourceURL(in value: String) -> String? {
        if Self.isValidSupportedSourceURL(value) {
            return value
        }
        return ClipboardURLExtractor.firstSupportedURL(in: value)
    }

    private nonisolated static func matchesSingleValuePath(_ path: String, prefix: String) -> Bool {
        guard path.hasPrefix(prefix) else {
            return false
        }
        var value = String(path.dropFirst(prefix.count))
        if value.hasSuffix("/") {
            value.removeLast()
        }
        for _ in 0..<8 {
            if value.isEmpty || value.contains("/") || value.contains("\\") {
                return false
            }
            guard let decodedValue = value.removingPercentEncoding else {
                return false
            }
            if decodedValue == value {
                return true
            }
            value = decodedValue
        }
        return false
    }

    nonisolated public static func isValidSupportedSourceURL(_ value: String) -> Bool {
        guard let components = URLComponents(string: value),
              let host = components.host?.lowercased(),
              let scheme = components.scheme?.lowercased()
        else {
            return false
        }
        guard ["http", "https"].contains(scheme) else {
            return false
        }
        if components.user != nil || components.password != nil {
            return false
        }
        if let port = components.port, ![80, 443].contains(port) {
            return false
        }
        let path = components.path
        switch host {
        case "x.com", "www.x.com", "twitter.com", "www.twitter.com":
            return path.contains("/status/")
        case "www.douyin.com":
            return Self.matchesSingleValuePath(path, prefix: "/video/")
        case "m.douyin.com", "www.iesdouyin.com":
            return Self.matchesSingleValuePath(path, prefix: "/share/video/")
        case "v.douyin.com":
            return Self.matchesSingleValuePath(path, prefix: "/")
        case "h5.pipix.com":
            return Self.matchesSingleValuePath(path, prefix: "/s/")
        case "www.pipix.com":
            return Self.matchesSingleValuePath(path, prefix: "/item/")
        case "www.xiaohongshu.com":
            return path.hasPrefix("/explore/") || path.hasPrefix("/discovery/item/")
        case "xhslink.com", "www.xhslink.com":
            return path != "/" && !path.isEmpty
        case "www.bilibili.com":
            return path.hasPrefix("/video/BV")
        case "youtube.com", "www.youtube.com", "m.youtube.com":
            if path == "/watch" || path == "/watch/" {
                return !(components.queryItems?.first(where: { $0.name == "v" })?.value?.isEmpty ?? true)
            }
            if path.hasPrefix("/shorts/") {
                let videoID = path.replacingOccurrences(of: "/shorts/", with: "").split(separator: "/", maxSplits: 1).first
                return !(videoID?.isEmpty ?? true)
            }
            return false
        case "youtu.be":
            let videoID = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            return !videoID.isEmpty && !videoID.contains("/")
        default:
            return false
        }
    }
}
