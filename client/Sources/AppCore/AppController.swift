import Foundation
import Observation

public protocol ClientAPI: Sendable {
    func registerDevice(name: String, platform: String, appVersion: String) async throws -> DeviceRegistration
    func createJob(url: String, preferredQuality: String?, token: String) async throws -> Job
    func listJobs(token: String) async throws -> [Job]
    func deleteJob(id: String, token: String) async throws -> Job
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
        let registration = try await apiClient.registerDevice(
            name: deviceName,
            platform: platform,
            appVersion: appVersion
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
            store.replaceJobs(jobs)
            try await jobsStore.saveJobs(jobs)
            store.setError(nil)
        } catch {
            store.setError(error.localizedDescription)
        }
    }

    public func submitCurrentURL(store: JobStore) async {
        guard !store.isLoading else {
            return
        }
        let url = store.draftURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !url.isEmpty else {
            store.setError("请先粘贴分享链接。")
            return
        }
        guard Self.isValidSupportedSourceURL(url) else {
            store.setError("请粘贴有效的 X、抖音、小红书、Bilibili 或 YouTube 公开链接。")
            return
        }
        store.setLoading(true)
        defer { store.setLoading(false) }
        do {
            if store.registration?.accessToken == nil {
                try await ensureRegistration(store: store)
            }
            guard let token = store.registration?.accessToken else {
                store.setError("设备初始化失败，请重试。")
                return
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
        } catch {
            store.setError(error.localizedDescription)
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
            return path.hasPrefix("/video/")
        case "v.douyin.com":
            return path != "/" && !path.isEmpty
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
