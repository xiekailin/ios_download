import AppCore
import Foundation
import Testing

actor MockRegistrationStore: RegistrationStore {
    var registration: DeviceRegistration?

    func loadRegistration() async throws -> DeviceRegistration? {
        registration
    }

    func saveRegistration(_ registration: DeviceRegistration) async throws {
        self.registration = registration
    }
}

actor MockJobsStore: JobsStore {
    var jobs: [Job] = []

    func loadJobs() async throws -> [Job] {
        jobs
    }

    func saveJobs(_ jobs: [Job]) async throws {
        self.jobs = jobs
    }
}

struct MockClientError: LocalizedError {
    let errorDescription: String?
}

actor ProgressRecorder {
    private(set) var events: [ArtifactDownloadProgress] = []

    func record(_ event: ArtifactDownloadProgress) {
        events.append(event)
    }
}

actor MockAPIClient: ClientAPI {
    var registration: DeviceRegistration
    var jobs: [Job]
    var createdJobs: [Job] = []
    var createdJobURLs: [String] = []
    var createdAudioDownloadJobURLs: [String] = []
    var createdJobPreferredQualities: [String?] = []
    var createJobErrorsByURL: [String: Error] = [:]
    var canceledJobIDs: [String] = []
    var deletedArtifactIDs: [String] = []
    var deletedJobIDs: [String] = []
    var deletedHistoryCalls = 0
    var deleteHistoryResult = DeleteHistoryResult(deletedCount: 0, skippedActiveCount: 0)
    var retriedJobIDs: [String] = []
    var registerCalls = 0
    var registeredBootstrapCodes: [String?] = []
    var createJobCalls = 0
    var createAudioDownloadJobCalls = 0
    var createAudioSeparationJobCalls = 0
    var createdAudioFileURLs: [URL] = []
    var artifacts: [ArtifactSummary] = []
    var requestedArtifactJobIDs: [String] = []
    var downloadedArtifactIDs: [String] = []
    var downloadProgressEvents: [ArtifactDownloadProgress] = []
    var downloadArtifactResult: DownloadedArtifact?
    var downloadArtifactError: Error?
    var cancelJobError: Error?
    var deleteArtifactError: Error?
    var deleteHistoryError: Error?
    var deleteJobError: Error?
    var retryJobError: Error?
    var previewResult = JobPreview(
        sourceURL: "https://x.com/demo/status/1",
        normalizedURL: "https://x.com/demo/status/1",
        provider: "yt-dlp",
        title: "Preview title",
        authorHandle: "author",
        thumbnailURL: nil,
        fileExtension: "mp4",
        recommendedJobType: .download,
        existingJobID: nil,
        existingArtifactID: nil,
        existingFileName: nil,
        existingLocalPath: nil,
        canReuseExisting: false
    )
    var previewError: Error?
    var previewedURLs: [String] = []
    var youtubeCookieStatusResult = YouTubeCookieStatus(isConfigured: false, fileSize: nil, updatedAt: nil)
    var youtubeCookieStatusCalls = 0
    var jobLogsResult = JobLogsResult(jobID: "job-1", items: [])
    var requestedLogJobIDs: [String] = []
    var uploadedYouTubeCookieFileURLs: [URL] = []
    var deleteYouTubeCookiesCalls = 0

    init(registration: DeviceRegistration, jobs: [Job], cancelJobError: Error? = nil, deleteJobError: Error? = nil, retryJobError: Error? = nil) {
        self.registration = registration
        self.jobs = jobs
        self.cancelJobError = cancelJobError
        self.deleteJobError = deleteJobError
        self.retryJobError = retryJobError
    }

    func registerDevice(name: String, platform: String, appVersion: String, bootstrapCode: String?) async throws -> DeviceRegistration {
        registerCalls += 1
        registeredBootstrapCodes.append(bootstrapCode)
        return registration
    }

    func previewJob(url: String, jobType: JobType, token: String) async throws -> JobPreview {
        previewedURLs.append(url)
        if let previewError {
            throw previewError
        }
        return previewResult
    }

    func createJob(url: String, preferredQuality: String?, token: String) async throws -> Job {
        createJobCalls += 1
        createdJobURLs.append(url)
        createdJobPreferredQualities.append(preferredQuality)
        if let error = createJobErrorsByURL[url] {
            throw error
        }
        let job = jobs[min(createdJobs.count, jobs.count - 1)]
        createdJobs.append(job)
        return job
    }

    func createAudioDownloadJob(url: String, token: String) async throws -> Job {
        createAudioDownloadJobCalls += 1
        createdAudioDownloadJobURLs.append(url)
        let job = jobs[0]
        createdJobs.append(job)
        return job
    }

    func createAudioSeparationJob(fileURL: URL, token: String) async throws -> Job {
        createAudioSeparationJobCalls += 1
        createdAudioFileURLs.append(fileURL)
        let job = jobs[0]
        createdJobs.append(job)
        return job
    }

    func youtubeCookieStatus(token: String) async throws -> YouTubeCookieStatus {
        youtubeCookieStatusCalls += 1
        return youtubeCookieStatusResult
    }

    func uploadYouTubeCookies(fileURL: URL, token: String) async throws -> YouTubeCookieStatus {
        uploadedYouTubeCookieFileURLs.append(fileURL)
        return youtubeCookieStatusResult
    }

    func deleteYouTubeCookies(token: String) async throws -> YouTubeCookieStatus {
        deleteYouTubeCookiesCalls += 1
        return YouTubeCookieStatus(isConfigured: false, fileSize: nil, updatedAt: nil)
    }

    func listJobs(token: String) async throws -> [Job] {
        jobs
    }

    func setCreateJobErrorsByURL(_ errors: [String: Error]) {
        createJobErrorsByURL = errors
    }

    func setPreviewResult(_ result: JobPreview) {
        previewResult = result
    }

    func setPreviewError(_ error: Error?) {
        previewError = error
    }

    func setArtifacts(_ artifacts: [ArtifactSummary]) {
        self.artifacts = artifacts
    }

    func setDeleteHistoryResult(_ result: DeleteHistoryResult) {
        deleteHistoryResult = result
    }

    func setYouTubeCookieStatusResult(_ result: YouTubeCookieStatus) {
        youtubeCookieStatusResult = result
    }

    func setJobLogsResult(_ result: JobLogsResult) {
        jobLogsResult = result
    }

    func setDeleteArtifactError(_ error: Error?) {
        deleteArtifactError = error
    }

    func setDeleteHistoryError(_ error: Error?) {
        deleteHistoryError = error
    }

    func listJobArtifacts(jobID: String, token: String) async throws -> [ArtifactSummary] {
        requestedArtifactJobIDs.append(jobID)
        return artifacts
    }

    func listJobLogs(jobID: String, token: String, limit: Int, afterID: Int?) async throws -> JobLogsResult {
        requestedLogJobIDs.append(jobID)
        return jobLogsResult
    }

    func cancelJob(id: String, token: String) async throws -> Job {
        canceledJobIDs.append(id)
        if let cancelJobError {
            throw cancelJobError
        }
        return jobs.first(where: { $0.id == id }) ?? jobs[0]
    }

    func deleteArtifact(id: String, token: String) async throws {
        deletedArtifactIDs.append(id)
        if let deleteArtifactError {
            throw deleteArtifactError
        }
    }

    func deleteHistory(token: String) async throws -> DeleteHistoryResult {
        deletedHistoryCalls += 1
        if let deleteHistoryError {
            throw deleteHistoryError
        }
        return deleteHistoryResult
    }

    func deleteJob(id: String, token: String) async throws -> Job {
        deletedJobIDs.append(id)
        if let deleteJobError {
            throw deleteJobError
        }
        return jobs.first(where: { $0.id == id }) ?? jobs[0]
    }

    func retryJob(id: String, token: String) async throws -> Job {
        retriedJobIDs.append(id)
        if let retryJobError {
            throw retryJobError
        }
        return jobs.first(where: { $0.id == id }) ?? jobs[0]
    }

    func setDownloadArtifactResult(_ result: DownloadedArtifact) {
        downloadArtifactResult = result
    }

    func setDownloadProgressEvents(_ events: [ArtifactDownloadProgress]) {
        downloadProgressEvents = events
    }

    func setDownloadArtifactError(_ error: Error?) {
        downloadArtifactError = error
    }

    func downloadArtifact(id: String, token: String) async throws -> DownloadedArtifact {
        downloadedArtifactIDs.append(id)
        if let downloadArtifactError {
            throw downloadArtifactError
        }
        if let downloadArtifactResult {
            return downloadArtifactResult
        }
        let url = FileManager.default.temporaryDirectory.appending(path: "artifact-\(UUID().uuidString).mp4")
        try Data().write(to: url)
        return DownloadedArtifact(temporaryURL: url, fileName: "artifact.mp4", mimeType: "video/mp4")
    }

    func downloadArtifact(
        id: String,
        token: String,
        onProgress: @Sendable @escaping (ArtifactDownloadProgress) async -> Void
    ) async throws -> DownloadedArtifact {
        downloadedArtifactIDs.append(id)
        if let downloadArtifactError {
            throw downloadArtifactError
        }
        for event in downloadProgressEvents {
            await onProgress(event)
        }
        if let downloadArtifactResult {
            return downloadArtifactResult
        }
        let url = FileManager.default.temporaryDirectory.appending(path: "artifact-\(UUID().uuidString).mp4")
        try Data().write(to: url)
        return DownloadedArtifact(temporaryURL: url, fileName: "artifact.mp4", mimeType: "video/mp4")
    }
}

func makeJob(id: String = "job-1", now: Date = Date()) -> Job {
    Job(
        id: id,
        deviceID: "device-1",
        sourceURL: "https://x.com/demo/status/1",
        normalizedURL: "https://x.com/demo/status/1",
        provider: nil,
        jobType: .download,
        status: .queued,
        progress: 0,
        errorCode: nil,
        errorMessage: nil,
        userMessage: nil,
        mediaTitle: nil,
        authorHandle: nil,
        thumbnailURL: nil,
        artifactID: nil,
        selectedQuality: nil,
        createdAt: now,
        updatedAt: now,
        finishedAt: nil
    )
}

func makeController(
    apiClient: MockAPIClient,
    registrationStore: MockRegistrationStore = MockRegistrationStore(),
    jobsStore: MockJobsStore = MockJobsStore()
) async -> AppController {
    await MainActor.run {
        AppController(
            apiClient: apiClient,
            registrationStore: registrationStore,
            jobsStore: jobsStore,
            deviceName: "iPhone",
            platform: "ios",
            appVersion: "0.1.0"
        )
    }
}

func makeStore(apiBaseURL: URL = URL(string: "http://127.0.0.1:18767")!, bootstrapCode: String? = nil) async -> JobStore {
    await MainActor.run {
        let store = JobStore()
        store.setSettings(AppSettings(apiBaseURL: apiBaseURL, autoPasteEnabled: true, preferredQuality: "720p", bootstrapCode: bootstrapCode))
        return store
    }
}

@Test func appControllerRegistersAndRefreshesJobs() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore(apiBaseURL: URL(string: "http://124.221.197.94:18767")!, bootstrapCode: "cloud-code")

    try await controller.ensureRegistration(store: store)
    await MainActor.run {
        #expect(store.registration?.deviceID == "device-1")
    }
    #expect(await apiClient.registeredBootstrapCodes == ["cloud-code"])
    await MainActor.run {
        store.draftURL = "https://x.com/demo/status/1"
    }
    let submittedJob = await controller.submitCurrentURL(store: store)
    await MainActor.run {
        #expect(submittedJob?.id == "job-1")
        #expect(store.jobs.count == 1)
        #expect(store.draftURL.isEmpty)
    }
    await controller.refreshJobs(store: store)
    await MainActor.run {
        #expect(store.jobs.count == 1)
        #expect(store.jobs[0].id == "job-1")
    }
}

@Test func appControllerListsJobLogs() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let result = JobLogsResult(
        jobID: "job-1",
        items: [
            JobLogEvent(id: 1, jobID: "job-1", level: "info", eventType: "queued", message: "任务已加入队列", createdAt: Date())
        ]
    )
    await apiClient.setJobLogsResult(result)
    let controller = await makeController(apiClient: apiClient)

    let logs = try await controller.listJobLogs(jobID: "job-1", token: "token-1")

    #expect(logs == result)
    #expect(await apiClient.requestedLogJobIDs == ["job-1"])
}

@Test func appControllerFetchesYouTubeCookieStatusAfterRegistration() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    await apiClient.setYouTubeCookieStatusResult(YouTubeCookieStatus(isConfigured: true, fileSize: 128, updatedAt: Date()))
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore(apiBaseURL: URL(string: "http://124.221.197.94:18767")!, bootstrapCode: "cloud-code")

    let status = await controller.refreshYouTubeCookieStatus(store: store)

    await MainActor.run {
        #expect(status?.isConfigured == true)
        #expect(store.youtubeCookieStatus?.isConfigured == true)
        #expect(store.errorMessage == nil)
    }
    #expect(await apiClient.registerCalls == 1)
    #expect(await apiClient.youtubeCookieStatusCalls == 1)
}

@Test func appControllerUploadsYouTubeCookiesAfterRegistration() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    await apiClient.setYouTubeCookieStatusResult(YouTubeCookieStatus(isConfigured: true, fileSize: 64, updatedAt: Date()))
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore(apiBaseURL: URL(string: "https://example.com")!, bootstrapCode: "cloud-code")
    let fileURL = FileManager.default.temporaryDirectory.appending(path: "cookies-\(UUID().uuidString).txt")
    try Data(".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\ttest-cookie\n".utf8).write(to: fileURL)
    defer { try? FileManager.default.removeItem(at: fileURL) }

    let status = await controller.uploadYouTubeCookies(fileURL: fileURL, store: store)

    await MainActor.run {
        #expect(status?.isConfigured == true)
        #expect(store.youtubeCookieStatus?.fileSize == 64)
        #expect(store.errorMessage == nil)
    }
    #expect(await apiClient.uploadedYouTubeCookieFileURLs == [fileURL])
}

@Test func appControllerRejectsInsecureCloudYouTubeCookieUpload() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore(apiBaseURL: URL(string: "http://124.221.197.94:18767")!, bootstrapCode: "cloud-code")
    let fileURL = FileManager.default.temporaryDirectory.appending(path: "cookies-\(UUID().uuidString).txt")
    try Data(".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\ttest-cookie\n".utf8).write(to: fileURL)
    defer { try? FileManager.default.removeItem(at: fileURL) }

    let status = await controller.uploadYouTubeCookies(fileURL: fileURL, store: store)

    await MainActor.run {
        #expect(status == nil)
        #expect(store.errorMessage == "为保护登录 Cookie，云端上传必须使用 HTTPS。")
    }
    #expect(await apiClient.uploadedYouTubeCookieFileURLs.isEmpty)
}

@Test func submitCurrentURLAcceptsBilibiliURL() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.draftURL = "https://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007.tianma.1-2-2.click"
    }
    _ = await controller.submitCurrentURL(store: store)

    await MainActor.run {
        #expect(store.jobs.count == 1)
        #expect(store.errorMessage == nil)
        #expect(store.draftURL.isEmpty)
    }
    #expect(await apiClient.createJobCalls == 1)
}

@Test func submitCurrentURLAcceptsYouTubeURL() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.draftURL = "https://www.youtube.com/watch?v=GEFehFHg_os"
    }
    _ = await controller.submitCurrentURL(store: store)

    await MainActor.run {
        #expect(store.jobs.count == 1)
        #expect(store.errorMessage == nil)
        #expect(store.draftURL.isEmpty)
    }
    #expect(await apiClient.createJobCalls == 1)
}

@Test func submitCurrentURLExtractsDouyinURLFromShareText() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.draftURL = "1.71 复制打开抖音，看看【兔一妈妈的作品】我的妈妈美如鲜花 # 人类幼崽迷之角度 # 亲子日... https://v.douyin.com/vaGFzBkNa_U/ 07/27 e@o.dN jPk:/"
    }
    _ = await controller.submitCurrentURL(store: store)

    await MainActor.run {
        #expect(store.jobs.count == 1)
        #expect(store.errorMessage == nil)
        #expect(store.draftURL.isEmpty)
    }
    #expect(await apiClient.createdJobURLs == ["https://v.douyin.com/vaGFzBkNa_U/"])
}

@Test func submitAudioDownloadURLExtractsDouyinURLFromShareText() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.draftURL = "1.71 复制打开抖音，看看【兔一妈妈的作品】我的妈妈美如鲜花 # 人类幼崽迷之角度 # 亲子日... https://v.douyin.com/vaGFzBkNa_U/ 07/27 e@o.dN jPk:/"
    }
    _ = await controller.submitAudioDownloadURL(store: store)

    await MainActor.run {
        #expect(store.jobs.count == 1)
        #expect(store.errorMessage == nil)
        #expect(store.draftURL.isEmpty)
    }
    #expect(await apiClient.createdAudioDownloadJobURLs == ["https://v.douyin.com/vaGFzBkNa_U/"])
}

@Test func submitCurrentURLRegistersDeviceBeforeCreatingJob() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.draftURL = "https://x.com/demo/status/1"
    }
    _ = await controller.submitCurrentURL(store: store)

    await MainActor.run {
        #expect(store.registration?.deviceID == "device-1")
        #expect(store.jobs.count == 1)
        #expect(store.draftURL.isEmpty)
        #expect(store.errorMessage == nil)
    }
    #expect(await apiClient.registerCalls == 1)
    #expect(await apiClient.createJobCalls == 1)
}

@Test func submitBatchURLsCreatesJobsOncePerExtractedURL() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let firstJob = makeJob(id: "job-1")
    let secondJob = makeJob(id: "job-2", now: Date().addingTimeInterval(1))
    let apiClient = MockAPIClient(registration: registration, jobs: [firstJob, secondJob])
    let jobsStore = MockJobsStore()
    let controller = await makeController(apiClient: apiClient, jobsStore: jobsStore)
    let store = await makeStore()

    await MainActor.run {
        store.batchDraftText = """
        第一条 https://x.com/demo/status/1
        第二条 https://www.youtube.com/watch?v=GEFehFHg_os
        https://x.com/demo/status/1
        """
    }
    let result = await controller.submitBatchURLs(store: store)

    await MainActor.run {
        #expect(result.requestedCount == 2)
        #expect(result.succeededCount == 2)
        #expect(result.failedCount == 0)
        #expect(result.jobs.map(\.id) == ["job-1", "job-2"])
        #expect(store.registration?.deviceID == "device-1")
        #expect(store.jobs.map(\.id) == ["job-2", "job-1"])
        #expect(store.batchDraftText.isEmpty)
        #expect(store.draftURL.isEmpty)
        #expect(store.errorMessage == nil)
        #expect(store.isPolling)
    }
    #expect(await apiClient.registerCalls == 1)
    #expect(await apiClient.createJobCalls == 2)
    #expect(await apiClient.createdJobURLs == ["https://x.com/demo/status/1", "https://www.youtube.com/watch?v=GEFehFHg_os"])
    #expect(await apiClient.createdJobPreferredQualities == ["720p", "720p"])
    #expect(await jobsStore.jobs.count == 2)
}

@Test func submitBatchURLsRejectsMissingValidURLsBeforeRegistration() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.batchDraftText = "普通文字\nhttps://example.com/skip"
    }
    let result = await controller.submitBatchURLs(store: store)

    await MainActor.run {
        #expect(result.requestedCount == 0)
        #expect(result.succeededCount == 0)
        #expect(store.errorMessage == "请粘贴至少一个有效的分享链接。")
        #expect(store.batchDraftText == "普通文字\nhttps://example.com/skip")
    }
    #expect(await apiClient.registerCalls == 0)
    #expect(await apiClient.createJobCalls == 0)
}

@Test func submitBatchURLsContinuesAfterSingleURLFailure() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let firstJob = makeJob(id: "job-1")
    let secondJob = makeJob(id: "job-2", now: Date().addingTimeInterval(1))
    let failingURL = "https://www.youtube.com/watch?v=GEFehFHg_os"
    let apiClient = MockAPIClient(registration: registration, jobs: [firstJob, secondJob])
    await apiClient.setCreateJobErrorsByURL([failingURL: MockClientError(errorDescription: "创建失败")])
    let jobsStore = MockJobsStore()
    let controller = await makeController(apiClient: apiClient, jobsStore: jobsStore)
    let store = await makeStore()

    await MainActor.run {
        store.batchDraftText = """
        https://x.com/demo/status/1
        \(failingURL)
        https://www.bilibili.com/video/BV1sRoHB5EHC/
        """
    }
    let result = await controller.submitBatchURLs(store: store)

    await MainActor.run {
        #expect(result.requestedCount == 3)
        #expect(result.succeededCount == 2)
        #expect(result.failedCount == 1)
        #expect(store.jobs.count == 2)
        #expect(store.batchDraftText == failingURL)
        #expect(store.errorMessage == "已创建 2 个任务，1 个链接失败，请检查后重试。")
        #expect(store.isPolling)
    }
    #expect(await apiClient.createdJobURLs == [
        "https://x.com/demo/status/1",
        failingURL,
        "https://www.bilibili.com/video/BV1sRoHB5EHC/",
    ])
    #expect(await jobsStore.jobs.count == 2)
}

@Test func submitBatchURLsRejectsMoreThanTwentyURLs() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.batchDraftText = (1...21).map { "https://x.com/demo/status/\($0)" }.joined(separator: "\n")
    }
    let result = await controller.submitBatchURLs(store: store)

    await MainActor.run {
        #expect(result.requestedCount == 21)
        #expect(result.succeededCount == 0)
        #expect(store.errorMessage == "单次最多支持 20 个链接。")
        #expect(!store.batchDraftText.isEmpty)
    }
    #expect(await apiClient.registerCalls == 0)
    #expect(await apiClient.createJobCalls == 0)
}

@Test func submitBatchURLsRejectsVeryLargeTextBeforeExtraction() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.batchDraftText = String(repeating: "普通文字", count: 22_000)
    }
    let result = await controller.submitBatchURLs(store: store)

    await MainActor.run {
        #expect(result.requestedCount == 0)
        #expect(result.succeededCount == 0)
        #expect(store.errorMessage == "批量文本过长，请减少后重试。")
        #expect(!store.batchDraftText.isEmpty)
    }
    #expect(await apiClient.registerCalls == 0)
    #expect(await apiClient.createJobCalls == 0)
}

@Test func submitAudioDownloadURLRegistersDeviceAndStoresJob() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let audioJob = Job(
        id: "audio-download-job-1",
        deviceID: "device-1",
        sourceURL: "https://www.youtube.com/watch?v=GEFehFHg_os",
        normalizedURL: "https://www.youtube.com/watch?v=GEFehFHg_os",
        provider: nil,
        jobType: .audioDownload,
        status: .queued,
        progress: 0,
        errorCode: nil,
        errorMessage: nil,
        userMessage: nil,
        mediaTitle: nil,
        authorHandle: nil,
        thumbnailURL: nil,
        artifactID: nil,
        selectedQuality: nil,
        createdAt: Date(),
        updatedAt: Date(),
        finishedAt: nil
    )
    let apiClient = MockAPIClient(registration: registration, jobs: [audioJob])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.draftURL = "https://www.youtube.com/watch?v=GEFehFHg_os"
    }
    let submittedJob = await controller.submitAudioDownloadURL(store: store)

    await MainActor.run {
        #expect(submittedJob?.id == "audio-download-job-1")
        #expect(store.registration?.deviceID == "device-1")
        #expect(store.jobs.count == 1)
        #expect(store.jobs[0].jobType == .audioDownload)
        #expect(store.draftURL.isEmpty)
        #expect(store.errorMessage == nil)
    }
    #expect(await apiClient.registerCalls == 1)
    #expect(await apiClient.createAudioDownloadJobCalls == 1)
}

@Test func submitAudioSeparationRegistersDeviceAndStoresJob() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let audioJob = Job(
        id: "audio-job-1",
        deviceID: "device-1",
        sourceURL: "upload:song.mp3",
        normalizedURL: "file:/tmp/song.mp3",
        provider: nil,
        jobType: .audioSeparation,
        status: .queued,
        progress: 0,
        errorCode: nil,
        errorMessage: nil,
        userMessage: nil,
        mediaTitle: "song",
        authorHandle: nil,
        thumbnailURL: nil,
        artifactID: nil,
        selectedQuality: nil,
        createdAt: Date(),
        updatedAt: Date(),
        finishedAt: nil
    )
    let apiClient = MockAPIClient(registration: registration, jobs: [audioJob])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()
    let fileURL = FileManager.default.temporaryDirectory.appending(path: "song-\(UUID().uuidString).mp3")
    try Data("audio".utf8).write(to: fileURL)
    defer { try? FileManager.default.removeItem(at: fileURL) }

    let submittedJob = await controller.submitAudioSeparation(fileURL: fileURL, store: store)

    await MainActor.run {
        #expect(submittedJob?.id == "audio-job-1")
        #expect(store.registration?.deviceID == "device-1")
        #expect(store.jobs.count == 1)
        #expect(store.jobs[0].jobType == .audioSeparation)
        #expect(store.errorMessage == nil)
    }
    #expect(await apiClient.registerCalls == 1)
    #expect(await apiClient.createAudioSeparationJobCalls == 1)
    #expect(await apiClient.createdAudioFileURLs == [fileURL])
}

@Test func submitAudioSeparationRejectsUnsupportedFileBeforeRegistration() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()
    let fileURL = FileManager.default.temporaryDirectory.appending(path: "notes-\(UUID().uuidString).txt")
    try Data("not audio".utf8).write(to: fileURL)
    defer { try? FileManager.default.removeItem(at: fileURL) }

    _ = await controller.submitAudioSeparation(fileURL: fileURL, store: store)

    await MainActor.run {
        #expect(store.errorMessage == "请选择 mp3、wav、m4a、aac 或 flac 音频文件。")
        #expect(store.jobs.isEmpty)
    }
    #expect(await apiClient.registerCalls == 0)
    #expect(await apiClient.createAudioSeparationJobCalls == 0)
}

@Test func listJobArtifactsReturnsAudioOutputs() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let now = Date()
    let artifacts = [
        ArtifactSummary(id: "vocals-1", jobID: "job-1", fileName: "song.vocals.wav", mimeType: "audio/wav", role: .vocals, fileSize: 10, createdAt: now),
        ArtifactSummary(id: "accompaniment-1", jobID: "job-1", fileName: "song.accompaniment.wav", mimeType: "audio/wav", role: .accompaniment, fileSize: 20, createdAt: now),
    ]
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    await apiClient.setArtifacts(artifacts)
    let controller = await makeController(apiClient: apiClient)

    let result = try await controller.listJobArtifacts(jobID: "job-1", token: "token-1")

    #expect(result.map(\.role) == [.vocals, .accompaniment])
    #expect(await apiClient.requestedArtifactJobIDs == ["job-1"])
}

@Test func downloadArtifactWithProgressForwardsEvents() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let temporaryURL = FileManager.default.temporaryDirectory.appending(path: "artifact-\(UUID().uuidString).mp4")
    try Data("video".utf8).write(to: temporaryURL)
    defer { try? FileManager.default.removeItem(at: temporaryURL) }
    let artifact = DownloadedArtifact(temporaryURL: temporaryURL, fileName: "demo.mp4", mimeType: "video/mp4")
    let progress = ArtifactDownloadProgress(
        receivedBytes: 50,
        totalBytes: 100,
        fraction: 0.5,
        bytesPerSecond: 25,
        etaSeconds: 2
    )
    await apiClient.setDownloadArtifactResult(artifact)
    await apiClient.setDownloadProgressEvents([progress])
    let controller = await makeController(apiClient: apiClient)
    let recorder = ProgressRecorder()

    let result = try await controller.downloadArtifact(id: "artifact-1", token: "token-1") { event in
        await recorder.record(event)
    }

    #expect(result == artifact)
    #expect(await recorder.events == [progress])
    #expect(await apiClient.downloadedArtifactIDs == ["artifact-1"])
}

@Test func jobAndArtifactTypesDecodeBackendValues() throws {
    #expect(try JSONDecoder().decode(JobType.self, from: Data(#""audio_download""#.utf8)) == .audioDownload)
    #expect(try JSONDecoder().decode(JobType.self, from: Data(#""audio_separation""#.utf8)) == .audioSeparation)
    #expect(try JSONDecoder().decode(ArtifactRole.self, from: Data(#""vocals""#.utf8)) == .vocals)
    #expect(try JSONDecoder().decode(ArtifactRole.self, from: Data(#""accompaniment""#.utf8)) == .accompaniment)
}

@Test func jobDecodesOldCacheWithoutJobType() throws {
    let data = Data(#"""
    {
      "id": "job-1",
      "deviceID": "device-1",
      "sourceURL": "https://x.com/demo/status/1",
      "normalizedURL": "https://x.com/demo/status/1",
      "provider": null,
      "status": "queued",
      "progress": 0,
      "downloadedBytes": null,
      "totalBytes": null,
      "speedBytesPerSec": null,
      "etaSeconds": null,
      "errorCode": null,
      "errorMessage": null,
      "userMessage": null,
      "mediaTitle": null,
      "authorHandle": null,
      "thumbnailURL": null,
      "artifactID": null,
      "selectedQuality": null,
      "createdAt": "2026-04-27T00:00:00Z",
      "updatedAt": "2026-04-27T00:00:00Z",
      "finishedAt": null
    }
    """#.utf8)
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601

    let job = try decoder.decode(Job.self, from: data)

    #expect(job.jobType == .download)
}

@Test func appSettingsDefaultStaysRemoteDevelopmentPort() {
    #expect(AppSettings().apiBaseURL.absoluteString == "http://127.0.0.1:8000")
}

@Test func startDoesNotFailWhenRegistrationIsMissing() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await controller.start(store: store)

    await MainActor.run {
        #expect(store.errorMessage == nil)
        #expect(store.registration == nil)
    }
    #expect(await apiClient.registerCalls == 0)
}

@Test func remoteRegistrationWithoutBootstrapCodeShowsLocalError() async throws {
    let apiClient = MockAPIClient(registration: DeviceRegistration(deviceID: "device-1", accessToken: "token-1"), jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore(apiBaseURL: URL(string: "http://124.221.197.94:18767")!)

    await MainActor.run {
        store.draftURL = "https://x.com/demo/status/1"
    }
    let job = await controller.submitCurrentURL(store: store)

    await MainActor.run {
        #expect(job == nil)
        #expect(store.errorMessage == "请输入服务器邀请码。")
    }
    #expect(await apiClient.registerCalls == 0)
}

@Test func ensureRegistrationUsesCachedRegistrationWithoutRegistering() async throws {
    let cachedRegistration = DeviceRegistration(deviceID: "cached-device", accessToken: "cached-token")
    let apiClient = MockAPIClient(registration: DeviceRegistration(deviceID: "device-1", accessToken: "token-1"), jobs: [makeJob()])
    let registrationStore = MockRegistrationStore()
    try await registrationStore.saveRegistration(cachedRegistration)
    let controller = await makeController(apiClient: apiClient, registrationStore: registrationStore)
    let store = await makeStore()

    try await controller.ensureRegistration(store: store)

    await MainActor.run {
        #expect(store.registration?.deviceID == "cached-device")
        #expect(store.registration?.accessToken == "cached-token")
    }
    #expect(await apiClient.registerCalls == 0)
}

@Test func cancelJobUpdatesLocalRecord() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let activeJob = makeJob(id: "job-1")
    let canceledJob = Job(
        id: "job-1",
        deviceID: "device-1",
        sourceURL: "https://x.com/demo/status/1",
        normalizedURL: "https://x.com/demo/status/1",
        provider: nil,
        jobType: .download,
        status: .canceled,
        progress: 0,
        errorCode: nil,
        errorMessage: nil,
        userMessage: nil,
        mediaTitle: nil,
        authorHandle: nil,
        thumbnailURL: nil,
        artifactID: nil,
        selectedQuality: nil,
        createdAt: Date(),
        updatedAt: Date(),
        finishedAt: Date()
    )
    let jobsStore = MockJobsStore()
    let apiClient = MockAPIClient(registration: registration, jobs: [canceledJob])
    let controller = await makeController(apiClient: apiClient, jobsStore: jobsStore)
    let store = await makeStore()

    await MainActor.run {
        store.setRegistration(registration)
        store.replaceJobs([activeJob])
    }
    await controller.cancelJob(id: "job-1", store: store)

    await MainActor.run {
        #expect(store.jobs.count == 1)
        #expect(store.jobs[0].status == .canceled)
        #expect(store.errorMessage == nil)
        #expect(!store.isLoading)
    }
    #expect(await apiClient.canceledJobIDs == ["job-1"])
    #expect(await jobsStore.jobs.first?.status == .canceled)
}

@Test func cancelJobWithoutRegistrationShowsError() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await controller.cancelJob(id: "job-1", store: store)

    await MainActor.run {
        #expect(store.errorMessage == "设备初始化失败，请重试。")
        #expect(!store.isLoading)
    }
    #expect(await apiClient.canceledJobIDs.isEmpty)
}

@Test func cancelJobFailureKeepsLocalRecord() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let activeJob = makeJob(id: "job-1")
    let apiClient = MockAPIClient(
        registration: registration,
        jobs: [activeJob],
        cancelJobError: MockClientError(errorDescription: "只有进行中的任务可以取消。")
    )
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.setRegistration(registration)
        store.replaceJobs([activeJob])
    }
    await controller.cancelJob(id: "job-1", store: store)

    await MainActor.run {
        #expect(store.jobs[0].status == .queued)
        #expect(store.errorMessage == "只有进行中的任务可以取消。")
        #expect(!store.isLoading)
    }
    #expect(await apiClient.canceledJobIDs == ["job-1"])
}

@Test func previewCurrentURLRegistersDeviceAndReturnsPreview() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    await apiClient.setPreviewResult(JobPreview(
        sourceURL: "https://x.com/demo/status/1",
        normalizedURL: "https://x.com/demo/status/1",
        provider: "yt-dlp",
        title: "Preview title",
        authorHandle: "author",
        thumbnailURL: "https://example.com/thumb.jpg",
        fileExtension: "mp4",
        recommendedJobType: .download,
        existingJobID: "job-existing",
        existingArtifactID: "artifact-existing",
        existingFileName: "existing.mp4",
        existingLocalPath: "/tmp/existing.mp4",
        canReuseExisting: true
    ))
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.draftURL = "https://x.com/demo/status/1"
    }
    let preview = await controller.previewCurrentURL(store: store)

    await MainActor.run {
        #expect(store.registration == registration)
        #expect(store.errorMessage == nil)
        #expect(!store.isLoading)
    }
    #expect(preview?.title == "Preview title")
    #expect(preview?.canReuseExisting == true)
    #expect(await apiClient.registerCalls == 1)
    #expect(await apiClient.previewedURLs == ["https://x.com/demo/status/1"])
    #expect(await apiClient.createJobCalls == 0)
}

@Test func previewCurrentURLFailureKeepsDraftAndDoesNotCreateJob() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    await apiClient.setPreviewError(MockClientError(errorDescription: "预览失败。"))
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.draftURL = "https://x.com/demo/status/1"
    }
    let preview = await controller.previewCurrentURL(store: store)

    await MainActor.run {
        #expect(preview == nil)
        #expect(store.draftURL == "https://x.com/demo/status/1")
        #expect(store.errorMessage == "预览失败。")
        #expect(!store.isLoading)
    }
    #expect(await apiClient.previewedURLs == ["https://x.com/demo/status/1"])
    #expect(await apiClient.createJobCalls == 0)
}

@Test func retryJobUpdatesLocalRecordAndStartsPolling() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let failedJob = Job(
        id: "job-1",
        deviceID: "device-1",
        sourceURL: "https://x.com/demo/status/1",
        normalizedURL: "https://x.com/demo/status/1",
        provider: nil,
        jobType: .download,
        status: .failed,
        progress: 42,
        errorCode: "download_failed",
        errorMessage: "raw error",
        userMessage: "下载失败。",
        mediaTitle: nil,
        authorHandle: nil,
        thumbnailURL: nil,
        artifactID: nil,
        selectedQuality: nil,
        createdAt: Date(),
        updatedAt: Date(),
        finishedAt: Date()
    )
    let retriedJob = makeJob(id: "job-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [retriedJob])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.setRegistration(registration)
        store.replaceJobs([failedJob])
    }
    await controller.retryJob(id: "job-1", store: store)

    await MainActor.run {
        #expect(store.jobs.count == 1)
        #expect(store.jobs[0].status == .queued)
        #expect(store.errorMessage == nil)
        #expect(store.isPolling)
    }
    #expect(await apiClient.retriedJobIDs == ["job-1"])
}

@Test func retryJobWithoutRegistrationShowsError() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await controller.retryJob(id: "job-1", store: store)

    await MainActor.run {
        #expect(store.errorMessage == "设备初始化失败，请重试。")
        #expect(!store.isLoading)
    }
    #expect(await apiClient.retriedJobIDs.isEmpty)
}

@Test func retryJobFailureKeepsLocalRecord() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let failedJob = Job(
        id: "job-1",
        deviceID: "device-1",
        sourceURL: "https://x.com/demo/status/1",
        normalizedURL: "https://x.com/demo/status/1",
        provider: nil,
        jobType: .download,
        status: .failed,
        progress: 42,
        errorCode: "download_failed",
        errorMessage: "raw error",
        userMessage: "下载失败。",
        mediaTitle: nil,
        authorHandle: nil,
        thumbnailURL: nil,
        artifactID: nil,
        selectedQuality: nil,
        createdAt: Date(),
        updatedAt: Date(),
        finishedAt: Date()
    )
    let apiClient = MockAPIClient(
        registration: registration,
        jobs: [makeJob(id: "job-1")],
        retryJobError: MockClientError(errorDescription: "只有失败任务可以重试。")
    )
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.setRegistration(registration)
        store.replaceJobs([failedJob])
    }
    await controller.retryJob(id: "job-1", store: store)

    await MainActor.run {
        #expect(store.jobs[0].status == .failed)
        #expect(store.errorMessage == "只有失败任务可以重试。")
        #expect(!store.isLoading)
    }
    #expect(await apiClient.retriedJobIDs == ["job-1"])
}

@Test func deleteArtifactKeepsLocalJobRecord() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let job = makeJob(id: "job-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [job])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.setRegistration(registration)
        store.replaceJobs([job])
    }
    let deleted = await controller.deleteArtifact(id: "artifact-1", store: store)

    await MainActor.run {
        #expect(deleted)
        #expect(store.jobs.count == 1)
        #expect(store.jobs[0].id == "job-1")
        #expect(store.errorMessage == nil)
    }
    #expect(await apiClient.deletedArtifactIDs == ["artifact-1"])
}

@Test func deleteArtifactFailureReturnsFalseAndKeepsError() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let job = makeJob(id: "job-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [job])
    await apiClient.setDeleteArtifactError(MockClientError(errorDescription: "删除源文件失败。"))
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.setRegistration(registration)
        store.replaceJobs([job])
    }
    let deleted = await controller.deleteArtifact(id: "artifact-1", store: store)

    await MainActor.run {
        #expect(!deleted)
        #expect(store.jobs.count == 1)
        #expect(store.errorMessage == "删除源文件失败。")
        #expect(!store.isLoading)
    }
    #expect(await apiClient.deletedArtifactIDs == ["artifact-1"])
}

@Test func deleteHistoryRemovesTerminalJobsAndKeepsActiveJobs() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let activeJob = makeJob(id: "job-active")
    let completedJob = Job(
        id: "job-completed",
        deviceID: "device-1",
        sourceURL: "https://x.com/demo/status/2",
        normalizedURL: "https://x.com/demo/status/2",
        provider: nil,
        jobType: .download,
        status: .completed,
        progress: 100,
        errorCode: nil,
        errorMessage: nil,
        userMessage: nil,
        mediaTitle: nil,
        authorHandle: nil,
        thumbnailURL: nil,
        artifactID: "artifact-1",
        selectedQuality: nil,
        createdAt: Date(),
        updatedAt: Date(),
        finishedAt: Date()
    )
    let jobsStore = MockJobsStore()
    let apiClient = MockAPIClient(registration: registration, jobs: [activeJob, completedJob])
    await apiClient.setDeleteHistoryResult(DeleteHistoryResult(deletedCount: 1, skippedActiveCount: 1, deletedJobIDs: ["job-completed"]))
    let controller = await makeController(apiClient: apiClient, jobsStore: jobsStore)
    let store = await makeStore()

    await MainActor.run {
        store.setRegistration(registration)
        store.replaceJobs([activeJob, completedJob])
    }
    let result = await controller.deleteHistory(store: store)

    await MainActor.run {
        #expect(store.jobs.map(\.id) == ["job-active"])
        #expect(store.errorMessage == nil)
    }
    #expect(result == DeleteHistoryResult(deletedCount: 1, skippedActiveCount: 1, deletedJobIDs: ["job-completed"]))
    #expect(await apiClient.deletedHistoryCalls == 1)
    #expect(await jobsStore.jobs.map(\.id) == ["job-active"])
}

@Test func deleteHistoryFailureKeepsLocalJobs() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let activeJob = makeJob(id: "job-active")
    let apiClient = MockAPIClient(registration: registration, jobs: [activeJob])
    await apiClient.setDeleteHistoryError(MockClientError(errorDescription: "批量删除失败。"))
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.setRegistration(registration)
        store.replaceJobs([activeJob])
    }
    let result = await controller.deleteHistory(store: store)

    await MainActor.run {
        #expect(result == nil)
        #expect(store.jobs.map(\.id) == ["job-active"])
        #expect(store.errorMessage == "批量删除失败。")
    }
}

@Test func deleteJobRemovesLocalRecord() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let firstJob = makeJob(id: "job-1")
    let secondJob = Job(
        id: "job-2",
        deviceID: "device-1",
        sourceURL: "https://www.douyin.com/video/123456",
        normalizedURL: "https://www.douyin.com/video/123456",
        provider: nil,
        jobType: .download,
        status: .completed,
        progress: 100,
        errorCode: nil,
        errorMessage: nil,
        userMessage: nil,
        mediaTitle: nil,
        authorHandle: nil,
        thumbnailURL: nil,
        artifactID: nil,
        selectedQuality: nil,
        createdAt: Date(),
        updatedAt: Date(),
        finishedAt: Date()
    )
    let apiClient = MockAPIClient(registration: registration, jobs: [firstJob, secondJob])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.setRegistration(registration)
        store.replaceJobs([firstJob, secondJob])
    }
    await controller.deleteJob(id: "job-1", store: store)

    await MainActor.run {
        #expect(store.jobs.count == 1)
        #expect(store.jobs[0].id == "job-2")
        #expect(store.errorMessage == nil)
    }
    #expect(await apiClient.deletedJobIDs == ["job-1"])
}

@Test func deleteJobFailureKeepsLocalRecord() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let firstJob = makeJob(id: "job-1")
    let secondJob = makeJob(id: "job-2", now: Date().addingTimeInterval(1))
    let apiClient = MockAPIClient(
        registration: registration,
        jobs: [firstJob, secondJob],
        deleteJobError: MockClientError(errorDescription: "当前任务不能删除。")
    )
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.setRegistration(registration)
        store.replaceJobs([firstJob, secondJob])
    }
    await controller.deleteJob(id: "job-1", store: store)

    await MainActor.run {
        #expect(store.jobs.count == 2)
        #expect(store.errorMessage == "当前任务不能删除。")
    }
    #expect(await apiClient.deletedJobIDs == ["job-1"])
}
