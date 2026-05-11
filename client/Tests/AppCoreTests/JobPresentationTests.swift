import AppCore
import Foundation
import Testing

private func makePresentationJob(
    status: JobStatus,
    errorCode: String? = nil,
    errorMessage: String? = nil,
    userMessage: String? = nil,
    downloadedBytes: Int? = nil,
    speedBytesPerSec: Int? = nil
) -> Job {
    let now = Date()
    return Job(
        id: "job-1",
        deviceID: "device-1",
        sourceURL: "https://x.com/demo/status/1",
        normalizedURL: "https://x.com/demo/status/1",
        provider: nil,
        status: status,
        progress: 0,
        downloadedBytes: downloadedBytes,
        speedBytesPerSec: speedBytesPerSec,
        errorCode: errorCode,
        errorMessage: errorMessage,
        userMessage: userMessage,
        mediaTitle: nil,
        authorHandle: nil,
        thumbnailURL: nil,
        artifactID: nil,
        selectedQuality: nil,
        createdAt: now,
        updatedAt: now,
        finishedAt: status.isTerminal ? now : nil
    )
}

@Test func failedJobDisplayErrorPrefersUserMessage() {
    let job = makePresentationJob(status: .failed, errorMessage: "raw error", userMessage: "请登录后重试。")

    #expect(job.displayErrorText == "请登录后重试。")
    #expect(job.secondaryStatusText == "请登录后重试。")
}

@Test func failedJobDisplayErrorDoesNotExposeTechnicalErrorMessage() {
    let job = makePresentationJob(status: .failed, errorMessage: "yt-dlp traceback: network timeout")

    #expect(job.displayErrorText == "任务失败，请重试。")
    #expect(job.secondaryStatusText == "任务失败，请重试。")
}

@Test func failedJobDisplayErrorUsesFriendlyFallback() {
    let job = makePresentationJob(status: .failed, errorCode: "download_failed")

    #expect(job.displayErrorText == "任务失败，请重试。")
    #expect(job.secondaryStatusText == "任务失败，请重试。")
}

@Test func failedJobRecoveryAdviceClassifiesRateLimit() {
    let job = makePresentationJob(status: .failed, errorMessage: "HTTP Error 429: Too Many Requests")

    #expect(job.failureRecoveryAdvice?.title == "平台正在限流")
    #expect(job.failureRecoveryAdvice?.detail.contains("稍后") == true)
    #expect(job.failureRecoveryAdvice?.actionTitle == "稍后重试")
    #expect(job.failureRecoveryAdvice?.action == .retry)
}

@Test func failedJobRecoveryAdviceClassifiesLoginVerification() {
    let job = makePresentationJob(status: .failed, errorMessage: "Sign in to confirm you're not a bot")

    #expect(job.failureRecoveryAdvice?.title == "需要登录验证")
    #expect(job.failureRecoveryAdvice?.detail.contains("Cookie") == true)
    #expect(job.failureRecoveryAdvice?.actionTitle == "选择 Cookie 并重试")
    #expect(job.failureRecoveryAdvice?.action == .uploadCookiesAndRetry)
}

@Test func failedJobRecoveryAdviceClassifiesDiskSpace() {
    let job = makePresentationJob(status: .failed, errorMessage: "No space left on device")

    #expect(job.failureRecoveryAdvice?.title == "磁盘空间不足")
    #expect(job.failureRecoveryAdvice?.detail.contains("释放") == true)
    #expect(job.failureRecoveryAdvice?.actionTitle == "打开下载目录")
    #expect(job.failureRecoveryAdvice?.action == .openDownloadsFolder)
}

@Test func failedJobRecoveryAdviceClassifiesNetworkRecovery() {
    let job = makePresentationJob(status: .failed, errorMessage: "connection reset by peer")

    #expect(job.failureRecoveryAdvice?.title == "网络连接不稳定")
    #expect(job.failureRecoveryAdvice?.action == .recheckBackendAndRetry)
}

@Test func activeJobDoesNotShowRecoveryAdvice() {
    let job = makePresentationJob(status: .downloading, errorMessage: "HTTP Error 429: Too Many Requests")

    #expect(job.failureRecoveryAdvice == nil)
}

@Test func canceledJobDisplayErrorTextIsFriendly() {
    let job = makePresentationJob(status: .canceled)

    #expect(job.displayErrorText == "任务已取消。")
    #expect(job.secondaryStatusText == "任务已取消。")
}

@Test func activeJobStillPrefersDownloadSummary() {
    let job = makePresentationJob(status: .downloading, userMessage: "正在下载", downloadedBytes: 1024)

    #expect(job.displayErrorText == nil)
    #expect(job.secondaryStatusText.contains("KB"))
}

@Test func metricsTextIncludesDownloadedSizeWhenTotalIsUnknown() {
    let job = makePresentationJob(status: .downloading, downloadedBytes: 1024, speedBytesPerSec: 2048)

    #expect(job.metricsText?.contains("已下载") == true)
    #expect(job.metricsText?.contains("速度") == true)
}

@Test func artifactMediaDetailsTextFormatsAvailableFields() {
    let artifact = ArtifactSummary(
        id: "artifact-1",
        jobID: "job-1",
        fileName: "video.mp4",
        mimeType: "video/mp4",
        role: .media,
        fileSize: 1024,
        thumbnailLocalPath: "/Users/test/Downloads/XDownloader/video.thumbnail.jpg",
        durationSeconds: 125,
        width: 1920,
        height: 1080,
        videoCodec: "h264",
        audioCodec: "aac",
        bitrateKbps: 4500,
        containerFormat: "mov,mp4,m4a,3gp,3g2,mj2",
        createdAt: Date()
    )

    #expect(artifact.mediaDetailsText == "02:05 · 1920×1080 · 4500 kbps · 视频 h264 · 音频 aac · mov")
    #expect(artifact.thumbnailLocalPath == "/Users/test/Downloads/XDownloader/video.thumbnail.jpg")
}

@Test func artifactSummaryDetectsPlayableVideoFromMimeType() {
    let artifact = ArtifactSummary(
        id: "artifact-1",
        jobID: "job-1",
        fileName: "video.mp4",
        mimeType: "video/mp4",
        role: .media,
        fileSize: 1024,
        createdAt: Date()
    )

    #expect(artifact.isPlayableVideo)
}

@Test func artifactSummaryDetectsPlayableVideoFromVideoCodec() {
    let artifact = ArtifactSummary(
        id: "artifact-1",
        jobID: "job-1",
        fileName: "video.bin",
        mimeType: "application/octet-stream",
        role: .media,
        fileSize: 1024,
        videoCodec: "h264",
        createdAt: Date()
    )

    #expect(artifact.isPlayableVideo)
}

@Test func artifactSummaryDoesNotTreatAudioOrSeparatedArtifactsAsPlayableVideo() {
    let audio = ArtifactSummary(
        id: "artifact-1",
        jobID: "job-1",
        fileName: "audio.mp3",
        mimeType: "audio/mpeg",
        role: .media,
        fileSize: 1024,
        createdAt: Date()
    )
    let vocals = ArtifactSummary(
        id: "artifact-2",
        jobID: "job-1",
        fileName: "vocals.wav",
        mimeType: "video/mp4",
        role: .vocals,
        fileSize: 1024,
        videoCodec: "h264",
        createdAt: Date()
    )

    #expect(!audio.isPlayableVideo)
    #expect(!vocals.isPlayableVideo)
}

@Test func materialLibraryFilterMatchesJobTypesAndFailures() {
    let video = makePresentationJob(status: .completed)
    let activeVideo = makePresentationJob(status: .downloading)
    let audio = Job(
        id: "job-2",
        deviceID: "device-1",
        sourceURL: "https://x.com/demo/status/2",
        normalizedURL: "https://x.com/demo/status/2",
        provider: nil,
        jobType: .audioDownload,
        status: .completed,
        progress: 100,
        errorCode: nil,
        errorMessage: nil,
        userMessage: nil,
        mediaTitle: "Audio",
        authorHandle: nil,
        thumbnailURL: nil,
        artifactID: nil,
        selectedQuality: nil,
        createdAt: Date(),
        updatedAt: Date(),
        finishedAt: Date()
    )
    let separation = Job(
        id: "job-3",
        deviceID: "device-1",
        sourceURL: "upload:song.mp3",
        normalizedURL: "file:/tmp/song.mp3",
        provider: nil,
        jobType: .audioSeparation,
        status: .failed,
        progress: 100,
        errorCode: nil,
        errorMessage: nil,
        userMessage: nil,
        mediaTitle: "Song",
        authorHandle: nil,
        thumbnailURL: nil,
        artifactID: nil,
        selectedQuality: nil,
        createdAt: Date(),
        updatedAt: Date(),
        finishedAt: Date()
    )
    let jobs = [video, activeVideo, audio, separation]

    #expect(MaterialLibraryFilter.video.matches(video))
    #expect(MaterialLibraryFilter.active.matches(activeVideo))
    #expect(MaterialLibraryFilter.completed.matches(video))
    #expect(!MaterialLibraryFilter.completed.matches(activeVideo))
    #expect(MaterialLibraryFilter.audio.matches(audio))
    #expect(MaterialLibraryFilter.separated.matches(separation))
    #expect(MaterialLibraryFilter.attention.matches(separation))
    #expect(jobs.filter(MaterialLibraryFilter.all.matches).count == 4)
    #expect(jobs.filter(MaterialLibraryFilter.audio.matches).map(\.id) == ["job-2"])
}

@Test func jobPresentationMetadataUsesNativeMacLabels() {
    #expect(JobType.download.presentationTitle == "视频")
    #expect(JobType.audioDownload.presentationSystemImage == "music.note")
    #expect(JobStatus.downloading.presentationTitle == "下载中")
    #expect(JobStatus.paused.presentationTitle == "已暂停")
    #expect(JobStatus.failed.presentationSystemImage == "exclamationmark.triangle.fill")
}

@Test func jobLogEventFormatsTimestamp() {
    let event = JobLogEvent(
        id: 1,
        jobID: "job-1",
        level: "info",
        eventType: "resolving",
        message: "开始解析链接",
        createdAt: Date(timeIntervalSince1970: 0)
    )

    #expect(event.levelTitle == "INFO")
    #expect(!event.createdAtText.isEmpty)
}

@Test func appSettingsDecodesOldCacheWithRemovedYouTubeCookiesFlag() throws {
    let json = #"{"apiBaseURL":"http://127.0.0.1:8000","autoPasteEnabled":true}"#.data(using: .utf8)!
    let decoder = JSONDecoder()

    let settings = try decoder.decode(AppSettings.self, from: json)

    #expect(settings.localBackendSecret.isEmpty)
    #expect(settings.bootstrapCode == nil)
    #expect(settings.autoSaveCompletedArtifactsToPhotos == false)
    #expect(settings.downloadPerformance == .balanced)
}

@Test func appSettingsDecodesAutoSaveCompletedArtifactsPreference() throws {
    let json = #"{"apiBaseURL":"http://127.0.0.1:8000","autoSaveCompletedArtifactsToPhotos":true}"#.data(using: .utf8)!
    let decoder = JSONDecoder()

    let settings = try decoder.decode(AppSettings.self, from: json)

    #expect(settings.autoSaveCompletedArtifactsToPhotos)
}

@Test func appSettingsDecodesDownloadPerformanceSettings() throws {
    let json = """
    {
      "apiBaseURL": "http://127.0.0.1:8000",
      "downloadPerformance": {
        "performanceMode": "performance",
        "directDownloadAccelerationEnabled": true,
        "directDownloadMaxConnections": 8,
        "directDownloadSegmentSizeBytes": 8388608,
        "simultaneousDownloadJobs": 3,
        "ytdlpConcurrentFragments": 8,
        "ffmpegThreadCount": 4,
        "downloadRateLimit": "5M"
      }
    }
    """.data(using: .utf8)!
    let decoder = JSONDecoder()

    let settings = try decoder.decode(AppSettings.self, from: json)

    #expect(settings.downloadPerformance.performanceMode == .performance)
    #expect(settings.downloadPerformance.directDownloadMaxConnections == 8)
    #expect(settings.downloadPerformance.directDownloadSegmentSizeBytes == 8 * 1024 * 1024)
    #expect(settings.downloadPerformance.simultaneousDownloadJobs == 3)
    #expect(settings.downloadPerformance.ytdlpConcurrentFragments == 8)
    #expect(settings.downloadPerformance.ffmpegThreadCount == 4)
    #expect(settings.downloadPerformance.downloadRateLimit == "5M")
}

@Test func downloadPerformanceModePresetsTuneBackendValues() {
    let lowPower = DownloadPerformanceSettings.defaults(for: .lowPower)
    let automaticLowPower = DownloadPerformanceSettings.automaticDefaults(
        activeProcessorCount: 10,
        isLowPowerModeEnabled: true,
        isThermallyConstrained: false,
        isExternalPowerConnected: true
    )
    let automaticBattery = DownloadPerformanceSettings.automaticDefaults(
        activeProcessorCount: 10,
        isLowPowerModeEnabled: false,
        isThermallyConstrained: false,
        isExternalPowerConnected: false
    )
    let automaticFast = DownloadPerformanceSettings.automaticDefaults(
        activeProcessorCount: 4,
        isLowPowerModeEnabled: false,
        isThermallyConstrained: false,
        isExternalPowerConnected: true
    )
    let performance = DownloadPerformanceSettings.defaults(for: .performance)

    #expect(lowPower.simultaneousDownloadJobs == 1)
    #expect(lowPower.directDownloadMaxConnectionsForBackend == 1)
    #expect(lowPower.ytdlpConcurrentFragments == 1)
    #expect(automaticLowPower.performanceMode == .automatic)
    #expect(automaticLowPower.simultaneousDownloadJobs == 1)
    #expect(automaticBattery.performanceMode == .automatic)
    #expect(automaticBattery.simultaneousDownloadJobs == 2)
    #expect(automaticBattery.directDownloadMaxConnectionsForBackend == 4)
    #expect(automaticFast.performanceMode == .automatic)
    #expect(automaticFast.simultaneousDownloadJobs == 4)
    #expect(automaticFast.directDownloadMaxConnectionsForBackend == 8)
    #expect(automaticFast.ytdlpConcurrentFragments == 8)
    #expect(performance.simultaneousDownloadJobs == 4)
    #expect(performance.directDownloadMaxConnectionsForBackend == 8)
    #expect(performance.ytdlpConcurrentFragments == 8)
    #expect(performance.directDownloadSegmentSizeBytes == 8 * 1024 * 1024)
}
