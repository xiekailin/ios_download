import Foundation

public enum BackendHealthStatus: Equatable, Sendable {
    case unknown
    case healthy
    case unhealthy
}

public enum JobStatus: String, Codable, Sendable, CaseIterable {
    case created
    case queued
    case resolving
    case resolved
    case downloading
    case muxing
    case storing
    case paused
    case completed
    case failed
    case canceled

    public var isTerminal: Bool {
        switch self {
        case .completed, .failed, .canceled:
            true
        default:
            false
        }
    }

    public var presentationTitle: String {
        switch self {
        case .created:
            "等待中"
        case .queued:
            "排队中"
        case .resolving:
            "解析中"
        case .resolved:
            "已解析"
        case .downloading:
            "下载中"
        case .muxing:
            "处理中"
        case .storing:
            "保存中"
        case .paused:
            "已暂停"
        case .completed:
            "已完成"
        case .failed:
            "失败"
        case .canceled:
            "已取消"
        }
    }

    public var presentationSystemImage: String {
        switch self {
        case .completed:
            "checkmark.circle.fill"
        case .failed:
            "exclamationmark.triangle.fill"
        case .canceled:
            "minus.circle.fill"
        case .downloading:
            "arrow.down.circle.fill"
        case .resolving, .resolved:
            "link.badge.plus"
        case .muxing, .storing:
            "gearshape.fill"
        case .paused:
            "pause.circle.fill"
        case .created, .queued:
            "clock.fill"
        }
    }
}

public enum JobType: String, Codable, Sendable, CaseIterable {
    case download
    case audioDownload = "audio_download"
    case audioSeparation = "audio_separation"

    public var presentationTitle: String {
        switch self {
        case .download:
            "视频"
        case .audioDownload:
            "MP3"
        case .audioSeparation:
            "音频拆分"
        }
    }

    public var presentationSystemImage: String {
        switch self {
        case .download:
            "film"
        case .audioDownload:
            "music.note"
        case .audioSeparation:
            "waveform"
        }
    }
}

public enum ArtifactRole: String, Codable, Sendable, CaseIterable {
    case media
    case vocals
    case accompaniment
}

public enum MaterialLibraryFilter: String, Codable, Sendable, CaseIterable, Identifiable {
    case all
    case active
    case completed
    case video
    case audio
    case separated
    case attention

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .all:
            "全部"
        case .active:
            "下载中"
        case .completed:
            "已完成"
        case .video:
            "视频"
        case .audio:
            "MP3"
        case .separated:
            "拆分"
        case .attention:
            "失败"
        }
    }

    public func matches(_ job: Job) -> Bool {
        switch self {
        case .all:
            true
        case .active:
            !job.status.isTerminal
        case .completed:
            job.status == .completed
        case .video:
            job.jobType == .download
        case .audio:
            job.jobType == .audioDownload
        case .separated:
            job.jobType == .audioSeparation
        case .attention:
            job.status == .failed || job.status == .canceled
        }
    }
}

public struct JobLogEvent: Codable, Identifiable, Sendable, Equatable {
    public let id: Int
    public let jobID: String
    public let level: String
    public let eventType: String
    public let message: String
    public let createdAt: Date

    public var levelTitle: String {
        level.uppercased()
    }

    public var createdAtText: String {
        Self.timeFormatter.string(from: createdAt)
    }

    private static let timeFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.timeStyle = .medium
        return formatter
    }()

    public init(id: Int, jobID: String, level: String, eventType: String, message: String, createdAt: Date) {
        self.id = id
        self.jobID = jobID
        self.level = level
        self.eventType = eventType
        self.message = message
        self.createdAt = createdAt
    }
}

public struct JobLogsResult: Codable, Sendable, Equatable {
    public let jobID: String
    public let items: [JobLogEvent]

    public init(jobID: String, items: [JobLogEvent]) {
        self.jobID = jobID
        self.items = items
    }
}

public struct DeviceRegistration: Codable, Sendable, Equatable {
    public let deviceID: String
    public let accessToken: String
    public let tokenType: String

    public init(deviceID: String, accessToken: String, tokenType: String = "bearer") {
        self.deviceID = deviceID
        self.accessToken = accessToken
        self.tokenType = tokenType
    }
}

public struct YouTubeCookieStatus: Codable, Sendable, Equatable {
    public let isConfigured: Bool
    public let fileSize: Int?
    public let updatedAt: Date?

    public init(isConfigured: Bool, fileSize: Int? = nil, updatedAt: Date? = nil) {
        self.isConfigured = isConfigured
        self.fileSize = fileSize
        self.updatedAt = updatedAt
    }
}

public struct DeleteHistoryResult: Codable, Sendable, Equatable {
    public let deletedCount: Int
    public let skippedActiveCount: Int
    public let deletedJobIDs: [String]

    public init(deletedCount: Int, skippedActiveCount: Int, deletedJobIDs: [String] = []) {
        self.deletedCount = deletedCount
        self.skippedActiveCount = skippedActiveCount
        self.deletedJobIDs = deletedJobIDs
    }
}

public struct JobPreview: Codable, Sendable, Equatable {
    public let sourceURL: String
    public let normalizedURL: String
    public let provider: String
    public let title: String?
    public let authorHandle: String?
    public let thumbnailURL: String?
    public let fileExtension: String
    public let recommendedJobType: JobType
    public let existingJobID: String?
    public let existingArtifactID: String?
    public let existingFileName: String?
    public let existingLocalPath: String?
    public let canReuseExisting: Bool

    public init(
        sourceURL: String,
        normalizedURL: String,
        provider: String,
        title: String?,
        authorHandle: String?,
        thumbnailURL: String?,
        fileExtension: String,
        recommendedJobType: JobType,
        existingJobID: String?,
        existingArtifactID: String?,
        existingFileName: String?,
        existingLocalPath: String?,
        canReuseExisting: Bool
    ) {
        self.sourceURL = sourceURL
        self.normalizedURL = normalizedURL
        self.provider = provider
        self.title = title
        self.authorHandle = authorHandle
        self.thumbnailURL = thumbnailURL
        self.fileExtension = fileExtension
        self.recommendedJobType = recommendedJobType
        self.existingJobID = existingJobID
        self.existingArtifactID = existingArtifactID
        self.existingFileName = existingFileName
        self.existingLocalPath = existingLocalPath
        self.canReuseExisting = canReuseExisting
    }

    public var displayTitle: String {
        title?.isEmpty == false ? title! : sourceURL
    }
}

public struct BatchSubmissionResult: Sendable, Equatable {
    public let requestedCount: Int
    public let succeededCount: Int
    public let failedCount: Int
    public let jobs: [Job]

    public init(requestedCount: Int, succeededCount: Int, failedCount: Int, jobs: [Job]) {
        self.requestedCount = requestedCount
        self.succeededCount = succeededCount
        self.failedCount = failedCount
        self.jobs = jobs
    }
}

public enum FailureRecoveryAction: String, Sendable, Equatable {
    case retry
    case uploadCookiesAndRetry
    case recheckBackendAndRetry
    case applyConservativeModeAndRetry
    case openDownloadsFolder
    case openSourceInBrowser
    case inspectLogs
}

public struct FailureRecoveryAdvice: Sendable, Equatable {
    public let title: String
    public let detail: String
    public let actionTitle: String
    public let action: FailureRecoveryAction
    public let secondaryActionTitle: String?
    public let secondaryAction: FailureRecoveryAction?

    public init(
        title: String,
        detail: String,
        actionTitle: String,
        action: FailureRecoveryAction,
        secondaryActionTitle: String? = nil,
        secondaryAction: FailureRecoveryAction? = nil
    ) {
        self.title = title
        self.detail = detail
        self.actionTitle = actionTitle
        self.action = action
        self.secondaryActionTitle = secondaryActionTitle
        self.secondaryAction = secondaryAction
    }
}

public struct Job: Codable, Identifiable, Sendable, Equatable {
    private enum CodingKeys: String, CodingKey {
        case id
        case deviceID
        case sourceURL
        case normalizedURL
        case provider
        case jobType
        case status
        case progress
        case priority
        case downloadedBytes
        case totalBytes
        case speedBytesPerSec
        case etaSeconds
        case errorCode
        case errorMessage
        case userMessage
        case mediaTitle
        case authorHandle
        case thumbnailURL
        case artifactID
        case selectedQuality
        case createdAt
        case updatedAt
        case finishedAt
    }

    public let id: String
    public let deviceID: String
    public let sourceURL: String
    public let normalizedURL: String
    public let provider: String?
    public let jobType: JobType
    public let status: JobStatus
    public let progress: Int
    public let priority: Int
    public let downloadedBytes: Int?
    public let totalBytes: Int?
    public let speedBytesPerSec: Int?
    public let etaSeconds: Int?
    public let errorCode: String?
    public let errorMessage: String?
    public let userMessage: String?
    public let mediaTitle: String?
    public let authorHandle: String?
    public let thumbnailURL: String?
    public let artifactID: String?
    public let selectedQuality: String?
    public let createdAt: Date
    public let updatedAt: Date
    public let finishedAt: Date?

    public init(
        id: String,
        deviceID: String,
        sourceURL: String,
        normalizedURL: String,
        provider: String?,
        jobType: JobType = .download,
        status: JobStatus,
        progress: Int,
        priority: Int = 0,
        downloadedBytes: Int? = nil,
        totalBytes: Int? = nil,
        speedBytesPerSec: Int? = nil,
        etaSeconds: Int? = nil,
        errorCode: String?,
        errorMessage: String?,
        userMessage: String?,
        mediaTitle: String?,
        authorHandle: String?,
        thumbnailURL: String?,
        artifactID: String?,
        selectedQuality: String?,
        createdAt: Date,
        updatedAt: Date,
        finishedAt: Date?
    ) {
        self.id = id
        self.deviceID = deviceID
        self.sourceURL = sourceURL
        self.normalizedURL = normalizedURL
        self.provider = provider
        self.jobType = jobType
        self.status = status
        self.progress = progress
        self.priority = priority
        self.downloadedBytes = downloadedBytes
        self.totalBytes = totalBytes
        self.speedBytesPerSec = speedBytesPerSec
        self.etaSeconds = etaSeconds
        self.errorCode = errorCode
        self.errorMessage = errorMessage
        self.userMessage = userMessage
        self.mediaTitle = mediaTitle
        self.authorHandle = authorHandle
        self.thumbnailURL = thumbnailURL
        self.artifactID = artifactID
        self.selectedQuality = selectedQuality
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.finishedAt = finishedAt
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.id = try container.decode(String.self, forKey: .id)
        self.deviceID = try container.decode(String.self, forKey: .deviceID)
        self.sourceURL = try container.decode(String.self, forKey: .sourceURL)
        self.normalizedURL = try container.decode(String.self, forKey: .normalizedURL)
        self.provider = try container.decodeIfPresent(String.self, forKey: .provider)
        self.jobType = try container.decodeIfPresent(JobType.self, forKey: .jobType) ?? .download
        self.status = try container.decode(JobStatus.self, forKey: .status)
        self.progress = try container.decode(Int.self, forKey: .progress)
        self.priority = try container.decodeIfPresent(Int.self, forKey: .priority) ?? 0
        self.downloadedBytes = try container.decodeIfPresent(Int.self, forKey: .downloadedBytes)
        self.totalBytes = try container.decodeIfPresent(Int.self, forKey: .totalBytes)
        self.speedBytesPerSec = try container.decodeIfPresent(Int.self, forKey: .speedBytesPerSec)
        self.etaSeconds = try container.decodeIfPresent(Int.self, forKey: .etaSeconds)
        self.errorCode = try container.decodeIfPresent(String.self, forKey: .errorCode)
        self.errorMessage = try container.decodeIfPresent(String.self, forKey: .errorMessage)
        self.userMessage = try container.decodeIfPresent(String.self, forKey: .userMessage)
        self.mediaTitle = try container.decodeIfPresent(String.self, forKey: .mediaTitle)
        self.authorHandle = try container.decodeIfPresent(String.self, forKey: .authorHandle)
        self.thumbnailURL = try container.decodeIfPresent(String.self, forKey: .thumbnailURL)
        self.artifactID = try container.decodeIfPresent(String.self, forKey: .artifactID)
        self.selectedQuality = try container.decodeIfPresent(String.self, forKey: .selectedQuality)
        self.createdAt = try container.decode(Date.self, forKey: .createdAt)
        self.updatedAt = try container.decode(Date.self, forKey: .updatedAt)
        self.finishedAt = try container.decodeIfPresent(Date.self, forKey: .finishedAt)
    }

    public var progressFraction: Double {
        max(0, min(Double(progress) / 100, 1))
    }

    public var progressText: String {
        "\(progress)%"
    }

    public var downloadedSizeText: String? {
        guard let downloadedBytes else { return nil }
        return Self.formatByteCount(downloadedBytes)
    }

    public var totalSizeText: String? {
        guard let totalBytes else { return nil }
        return Self.formatByteCount(totalBytes)
    }

    public var downloadedSummaryText: String? {
        guard let downloadedSizeText else { return nil }
        if let totalSizeText {
            return "\(downloadedSizeText) / \(totalSizeText)"
        }
        return downloadedSizeText
    }

    public var speedText: String? {
        guard let speedBytesPerSec else { return nil }
        return "\(Self.formatByteCount(speedBytesPerSec))/s"
    }

    public var etaText: String? {
        guard let etaSeconds, etaSeconds >= 0 else { return nil }
        if etaSeconds >= 3600 {
            let hours = etaSeconds / 3600
            let minutes = (etaSeconds % 3600) / 60
            let seconds = etaSeconds % 60
            return String(format: "%02d:%02d:%02d", hours, minutes, seconds)
        }
        let minutes = etaSeconds / 60
        let seconds = etaSeconds % 60
        return String(format: "%02d:%02d", minutes, seconds)
    }

    public var displayErrorText: String? {
        switch status {
        case .failed:
            if let userMessage, !userMessage.isEmpty {
                return userMessage
            }
            return "任务失败，请重试。"
        case .canceled:
            return "任务已取消。"
        default:
            return nil
        }
    }

    public var failureRecoveryAdvice: FailureRecoveryAdvice? {
        guard status == .failed else { return nil }

        let context = [errorCode, errorMessage, userMessage]
            .compactMap { $0?.lowercased() }
            .joined(separator: " ")

        if Self.failureContext(context, containsAny: [
            "sign in",
            "login",
            "cookies",
            "cookie",
            "not a bot",
            "confirm your age",
            "inappropriate for some users",
            "需要登录验证",
        ]) {
            return FailureRecoveryAdvice(
                title: "需要登录验证",
                detail: "平台要求登录或真人验证，请在设置里更新有效 Cookie 后重试。",
                actionTitle: "选择 Cookie 并重试",
                action: .uploadCookiesAndRetry,
                secondaryActionTitle: "查看日志",
                secondaryAction: .inspectLogs
            )
        }

        if Self.failureContext(context, containsAny: [
            "http error 429",
            "too many requests",
            "rate limit",
            "ratelimit",
            "限流",
        ]) {
            return FailureRecoveryAdvice(
                title: "平台正在限流",
                detail: "我可以把下载配置切到稳妥模式，重启本地后端后再重试，减少再次触发平台限流的概率。",
                actionTitle: "降速修复并重试",
                action: .applyConservativeModeAndRetry,
                secondaryActionTitle: "只重试任务",
                secondaryAction: .retry
            )
        }

        if Self.failureContext(context, containsAny: [
            "no space left",
            "not enough space",
            "disk full",
            "device full",
            "磁盘空间不足",
        ]) {
            return FailureRecoveryAdvice(
                title: "磁盘空间不足",
                detail: "本地保存或合并文件时空间不够，请释放下载目录所在磁盘空间后重试。",
                actionTitle: "打开下载目录",
                action: .openDownloadsFolder,
                secondaryActionTitle: "清理后重试",
                secondaryAction: .retry
            )
        }

        if Self.failureContext(context, containsAny: [
            "timed out",
            "timeout",
            "connection reset",
            "service unavailable",
            "temporary failure",
            "network",
            "网络超时",
            "网络",
        ]) {
            return FailureRecoveryAdvice(
                title: "网络连接不稳定",
                detail: "当前网络或平台响应不稳定，请保持后端运行并稍后重试。",
                actionTitle: "检测并重试",
                action: .recheckBackendAndRetry,
                secondaryActionTitle: "查看日志",
                secondaryAction: .inspectLogs
            )
        }

        if Self.failureContext(context, containsAny: [
            "requested format",
            "format is not available",
            "no video formats",
            "格式不可用",
        ]) {
            return FailureRecoveryAdvice(
                title: "视频格式不可用",
                detail: "平台暂时没有提供可下载格式，可以换一个清晰度或稍后重试。",
                actionTitle: "重试任务",
                action: .retry,
                secondaryActionTitle: "查看日志",
                secondaryAction: .inspectLogs
            )
        }

        if Self.failureContext(context, containsAny: [
            "private",
            "unavailable",
            "has been removed",
            "made this video available",
            "not available",
            "不可访问",
            "私密",
            "已被删除",
            "需要权限",
        ]) {
            return FailureRecoveryAdvice(
                title: "素材不可访问",
                detail: "原链接可能已删除、设为私密或需要账号权限，请确认链接可在浏览器中正常打开。",
                actionTitle: "打开源链接",
                action: .openSourceInBrowser,
                secondaryActionTitle: "查看日志",
                secondaryAction: .inspectLogs
            )
        }

        if Self.failureContext(context, containsAny: [
            "ffmpeg",
            "merge failed",
            "mux",
            "postprocessing",
            "conversion failed",
            "合并",
            "转码",
        ]) {
            return FailureRecoveryAdvice(
                title: "合并处理失败",
                detail: "下载已到后处理阶段但合并失败，请确认 FFmpeg 可用并重试。",
                actionTitle: "重试任务",
                action: .retry,
                secondaryActionTitle: "查看日志",
                secondaryAction: .inspectLogs
            )
        }

        return FailureRecoveryAdvice(
            title: "建议重试",
            detail: "可以直接重试任务；如果连续失败，请打开日志查看最后一条错误。",
            actionTitle: "重试任务",
            action: .retry,
            secondaryActionTitle: "查看日志",
            secondaryAction: .inspectLogs
        )
    }

    public var secondaryStatusText: String {
        if let displayErrorText {
            return displayErrorText
        }
        if status == .paused {
            return "任务已暂停，点击继续后会重新进入队列。"
        }
        if let downloadedSummaryText {
            return downloadedSummaryText
        }
        if let userMessage {
            return userMessage
        }
        if let authorHandle {
            return authorHandle
        }
        return "等待下载"
    }

    public var metricsText: String? {
        let downloadedText = totalBytes == nil ? downloadedSizeText.map { "已下载 \($0)" } : nil
        let items = [
            progressText,
            downloadedText,
            speedText.map { "速度 \($0)" },
            etaText.map { "剩余 \($0)" },
        ].compactMap { $0 }
        guard !items.isEmpty else { return nil }
        return items.joined(separator: " · ")
    }

    private static func formatByteCount(_ value: Int) -> String {
        let formatter = ByteCountFormatter()
        formatter.allowedUnits = [.useKB, .useMB, .useGB]
        formatter.countStyle = .file
        formatter.includesUnit = true
        formatter.isAdaptive = true
        formatter.zeroPadsFractionDigits = false
        return formatter.string(fromByteCount: Int64(value))
    }

    private static func failureContext(_ context: String, containsAny markers: [String]) -> Bool {
        markers.contains { context.contains($0) }
    }
}

public enum JobQueueLane: String, CaseIterable, Identifiable, Sendable, Equatable {
    case running
    case queued
    case paused

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .running:
            "正在处理"
        case .queued:
            "排队中"
        case .paused:
            "已暂停"
        }
    }

    public static func lane(for status: JobStatus) -> JobQueueLane? {
        switch status {
        case .created, .queued:
            .queued
        case .paused:
            .paused
        case .resolving, .resolved, .downloading, .muxing, .storing:
            .running
        case .completed, .failed, .canceled:
            nil
        }
    }
}

public struct JobQueuePlatformSummary: Identifiable, Sendable, Equatable {
    public var id: String { title }
    public let title: String
    public let count: Int

    public init(title: String, count: Int) {
        self.title = title
        self.count = count
    }
}

public struct JobQueueOverview: Sendable, Equatable {
    public let runningCount: Int
    public let queuedCount: Int
    public let pausedCount: Int
    public let platformSummaries: [JobQueuePlatformSummary]

    public var totalActiveCount: Int {
        runningCount + queuedCount + pausedCount
    }

    public init(jobs: [Job]) {
        var runningCount = 0
        var queuedCount = 0
        var pausedCount = 0
        var platformCounts: [String: Int] = [:]

        for job in jobs {
            guard let lane = JobQueueLane.lane(for: job.status) else { continue }
            switch lane {
            case .running:
                runningCount += 1
            case .queued:
                queuedCount += 1
            case .paused:
                pausedCount += 1
            }
            platformCounts[job.queuePlatformTitle, default: 0] += 1
        }

        self.runningCount = runningCount
        self.queuedCount = queuedCount
        self.pausedCount = pausedCount
        self.platformSummaries = platformCounts
            .map { JobQueuePlatformSummary(title: $0.key, count: $0.value) }
            .sorted { left, right in
                if left.count != right.count {
                    return left.count > right.count
                }
                return left.title.localizedStandardCompare(right.title) == .orderedAscending
            }
    }
}

public extension Job {
    var queuePlatformTitle: String {
        if let providerTitle = Self.platformTitle(provider: provider) {
            return providerTitle
        }
        guard let host = URL(string: sourceURL)?.host?.lowercased() else {
            return "未知平台"
        }
        return Self.platformTitle(host: host) ?? host.removingWWWPrefix()
    }

    private static func platformTitle(provider: String?) -> String? {
        guard let provider, !provider.isEmpty else { return nil }
        return platformTitle(key: provider)
    }

    private static func platformTitle(host: String) -> String? {
        if host == "youtu.be" || host.hasSuffix("youtube.com") {
            return "YouTube"
        }
        if host.hasSuffix("bilibili.com") {
            return "Bilibili"
        }
        if host.hasSuffix("douyin.com") {
            return "Douyin"
        }
        if host == "x.com" || host.hasSuffix("twitter.com") {
            return "X"
        }
        if host.hasSuffix("tiktok.com") {
            return "TikTok"
        }
        return nil
    }

    private static func platformTitle(key: String) -> String? {
        switch key.lowercased() {
        case "youtube", "youtu", "yt":
            "YouTube"
        case "bilibili", "bili":
            "Bilibili"
        case "douyin":
            "Douyin"
        case "x", "twitter":
            "X"
        case "tiktok":
            "TikTok"
        default:
            nil
        }
    }
}

private extension String {
    func removingWWWPrefix() -> String {
        if hasPrefix("www.") {
            return String(dropFirst(4))
        }
        return self
    }
}

public struct ArtifactSummary: Codable, Identifiable, Sendable, Equatable {
    public let id: String
    public let jobID: String
    public let fileName: String
    public let mimeType: String
    public let role: ArtifactRole
    public let fileSize: Int
    public let localPath: String?
    public let thumbnailLocalPath: String?
    public let durationSeconds: Double?
    public let width: Int?
    public let height: Int?
    public let videoCodec: String?
    public let audioCodec: String?
    public let bitrateKbps: Int?
    public let containerFormat: String?
    public let createdAt: Date

    public var isPlayableVideo: Bool {
        role == .media && (mimeType.lowercased().hasPrefix("video/") || videoCodec != nil)
    }

    public var mediaDetailsText: String? {
        let durationText = durationSeconds.map { Self.formatDuration($0) }
        let resolutionText = width.flatMap { width in height.map { "\(width)×\($0)" } }
        let bitrateText = bitrateKbps.map { "\($0) kbps" }
        let videoText = videoCodec.map { "视频 \($0)" }
        let audioText = audioCodec.map { "音频 \($0)" }
        let containerText = containerFormat?.split(separator: ",").first.map(String.init)
        let items = [durationText, resolutionText, bitrateText, videoText, audioText, containerText].compactMap { $0 }
        return items.isEmpty ? nil : items.joined(separator: " · ")
    }

    public var fileSizeText: String {
        Self.formatByteCount(fileSize)
    }

    public var createdAtText: String {
        Self.dateFormatter.string(from: createdAt)
    }

    public var detailItems: [(label: String, value: String)] {
        var items: [(String, String)] = [("大小", fileSizeText)]
        if let durationSeconds {
            items.append(("时长", Self.formatDuration(durationSeconds)))
        }
        if let width, let height {
            items.append(("分辨率", "\(width)×\(height)"))
        }
        if let videoCodec {
            items.append(("视频编码", videoCodec))
        }
        if let audioCodec {
            items.append(("音频编码", audioCodec))
        }
        if let bitrateKbps {
            items.append(("码率", "\(bitrateKbps) kbps"))
        }
        if let containerText = containerFormat?.split(separator: ",").first.map(String.init) {
            items.append(("容器", containerText))
        }
        items.append(("保存时间", createdAtText))
        if let localPath {
            items.append(("本地路径", localPath))
        }
        return items
    }

    private static let dateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter
    }()

    private static func formatByteCount(_ value: Int) -> String {
        let formatter = ByteCountFormatter()
        formatter.allowedUnits = [.useKB, .useMB, .useGB]
        formatter.countStyle = .file
        formatter.includesUnit = true
        formatter.isAdaptive = true
        formatter.zeroPadsFractionDigits = false
        return formatter.string(fromByteCount: Int64(value))
    }

    private static func formatDuration(_ value: Double) -> String {
        let seconds = max(0, Int(value.rounded()))
        if seconds >= 3600 {
            return String(format: "%02d:%02d:%02d", seconds / 3600, seconds % 3600 / 60, seconds % 60)
        }
        return String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }

    public init(
        id: String,
        jobID: String,
        fileName: String,
        mimeType: String,
        role: ArtifactRole,
        fileSize: Int,
        localPath: String? = nil,
        thumbnailLocalPath: String? = nil,
        durationSeconds: Double? = nil,
        width: Int? = nil,
        height: Int? = nil,
        videoCodec: String? = nil,
        audioCodec: String? = nil,
        bitrateKbps: Int? = nil,
        containerFormat: String? = nil,
        createdAt: Date
    ) {
        self.id = id
        self.jobID = jobID
        self.fileName = fileName
        self.mimeType = mimeType
        self.role = role
        self.fileSize = fileSize
        self.localPath = localPath
        self.thumbnailLocalPath = thumbnailLocalPath
        self.durationSeconds = durationSeconds
        self.width = width
        self.height = height
        self.videoCodec = videoCodec
        self.audioCodec = audioCodec
        self.bitrateKbps = bitrateKbps
        self.containerFormat = containerFormat
        self.createdAt = createdAt
    }
}

public struct DownloadedArtifact: Sendable, Equatable {
    public let temporaryURL: URL
    public let fileName: String
    public let mimeType: String?

    public init(temporaryURL: URL, fileName: String, mimeType: String? = nil) {
        self.temporaryURL = temporaryURL
        self.fileName = fileName
        self.mimeType = mimeType
    }
}

public struct ArtifactDownloadProgress: Sendable, Equatable {
    public let receivedBytes: Int64
    public let totalBytes: Int64?
    public let fraction: Double?
    public let bytesPerSecond: Double?
    public let etaSeconds: Double?

    public init(
        receivedBytes: Int64,
        totalBytes: Int64?,
        fraction: Double?,
        bytesPerSecond: Double?,
        etaSeconds: Double?
    ) {
        self.receivedBytes = receivedBytes
        self.totalBytes = totalBytes
        self.fraction = fraction
        self.bytesPerSecond = bytesPerSecond
        self.etaSeconds = etaSeconds
    }
}

public enum ArtifactDownloadEvent: Sendable, Equatable {
    case progress(ArtifactDownloadProgress)
    case finished(DownloadedArtifact)
}

public enum DownloadPerformanceMode: String, Codable, CaseIterable, Identifiable, Sendable {
    case automatic
    case lowPower
    case balanced
    case performance

    public var id: String { rawValue }

    public var backendValue: String {
        switch self {
        case .automatic:
            "auto"
        case .lowPower:
            "low_power"
        case .balanced:
            "balanced"
        case .performance:
            "performance"
        }
    }

    public var title: String {
        switch self {
        case .automatic:
            "自动"
        case .lowPower:
            "省电"
        case .balanced:
            "均衡"
        case .performance:
            "高速"
        }
    }
}

public struct DownloadPerformanceSettings: Codable, Sendable, Equatable {
    private enum CodingKeys: String, CodingKey {
        case performanceMode
        case directDownloadAccelerationEnabled
        case directDownloadMaxConnections
        case directDownloadSegmentSizeBytes
        case simultaneousDownloadJobs
        case ytdlpConcurrentFragments
        case ffmpegThreadCount
        case downloadRateLimit
        case nightDownloadEnabled
        case nightDownloadStartHour
        case nightDownloadEndHour
    }

    public static let balanced = DownloadPerformanceSettings()

    public var performanceMode: DownloadPerformanceMode
    public var directDownloadAccelerationEnabled: Bool
    public var directDownloadMaxConnections: Int
    public var directDownloadSegmentSizeBytes: Int
    public var simultaneousDownloadJobs: Int
    public var ytdlpConcurrentFragments: Int
    public var ffmpegThreadCount: Int
    public var downloadRateLimit: String
    public var nightDownloadEnabled: Bool
    public var nightDownloadStartHour: Int
    public var nightDownloadEndHour: Int

    public static func defaults(for mode: DownloadPerformanceMode) -> DownloadPerformanceSettings {
        switch mode {
        case .automatic:
            automaticDefaultsForCurrentDevice()
        case .lowPower:
            DownloadPerformanceSettings(
                performanceMode: .lowPower,
                directDownloadAccelerationEnabled: false,
                directDownloadMaxConnections: 1,
                directDownloadSegmentSizeBytes: 4 * 1024 * 1024,
                simultaneousDownloadJobs: 1,
                ytdlpConcurrentFragments: 1,
                ffmpegThreadCount: 1,
                downloadRateLimit: ""
            )
        case .balanced:
            .balanced
        case .performance:
            DownloadPerformanceSettings(
                performanceMode: .performance,
                directDownloadAccelerationEnabled: true,
                directDownloadMaxConnections: 8,
                directDownloadSegmentSizeBytes: 8 * 1024 * 1024,
                simultaneousDownloadJobs: 4,
                ytdlpConcurrentFragments: 8,
                ffmpegThreadCount: 0,
                downloadRateLimit: ""
            )
        }
    }

    public static func automaticDefaults(
        activeProcessorCount: Int,
        isLowPowerModeEnabled: Bool,
        isThermallyConstrained: Bool,
        isExternalPowerConnected: Bool = true
    ) -> DownloadPerformanceSettings {
        let baseMode: DownloadPerformanceMode
        if isLowPowerModeEnabled || isThermallyConstrained {
            baseMode = .lowPower
        } else if !isExternalPowerConnected {
            baseMode = .balanced
        } else {
            baseMode = .performance
        }
        var settings = defaults(for: baseMode)
        settings.performanceMode = .automatic
        return settings
    }

    public static func automaticDefaultsForCurrentDevice(isExternalPowerConnected: Bool = true) -> DownloadPerformanceSettings {
        let processInfo = ProcessInfo.processInfo
        let isThermallyConstrained: Bool
        switch processInfo.thermalState {
        case .serious, .critical:
            isThermallyConstrained = true
        case .nominal, .fair:
            isThermallyConstrained = false
        @unknown default:
            isThermallyConstrained = false
        }
        return automaticDefaults(
            activeProcessorCount: processInfo.activeProcessorCount,
            isLowPowerModeEnabled: processInfo.isLowPowerModeEnabled,
            isThermallyConstrained: isThermallyConstrained,
            isExternalPowerConnected: isExternalPowerConnected
        )
    }

    public func resolvedForCurrentDevice(isExternalPowerConnected: Bool = true) -> DownloadPerformanceSettings {
        guard performanceMode == .automatic else { return self }
        var settings = Self.automaticDefaultsForCurrentDevice(isExternalPowerConnected: isExternalPowerConnected)
        settings.downloadRateLimit = downloadRateLimit
        settings.nightDownloadEnabled = nightDownloadEnabled
        settings.nightDownloadStartHour = nightDownloadStartHour
        settings.nightDownloadEndHour = nightDownloadEndHour
        return settings
    }

    public init(
        performanceMode: DownloadPerformanceMode = .balanced,
        directDownloadAccelerationEnabled: Bool = true,
        directDownloadMaxConnections: Int = 4,
        directDownloadSegmentSizeBytes: Int = 4 * 1024 * 1024,
        simultaneousDownloadJobs: Int = 2,
        ytdlpConcurrentFragments: Int = 4,
        ffmpegThreadCount: Int = 0,
        downloadRateLimit: String = "",
        nightDownloadEnabled: Bool = false,
        nightDownloadStartHour: Int = 23,
        nightDownloadEndHour: Int = 7
    ) {
        self.performanceMode = performanceMode
        self.directDownloadAccelerationEnabled = directDownloadAccelerationEnabled
        self.directDownloadMaxConnections = max(1, directDownloadMaxConnections)
        self.directDownloadSegmentSizeBytes = max(1024 * 1024, directDownloadSegmentSizeBytes)
        self.simultaneousDownloadJobs = max(1, simultaneousDownloadJobs)
        self.ytdlpConcurrentFragments = max(1, ytdlpConcurrentFragments)
        self.ffmpegThreadCount = max(0, ffmpegThreadCount)
        self.downloadRateLimit = downloadRateLimit.trimmingCharacters(in: .whitespacesAndNewlines)
        self.nightDownloadEnabled = nightDownloadEnabled
        self.nightDownloadStartHour = min(23, max(0, nightDownloadStartHour))
        self.nightDownloadEndHour = min(23, max(0, nightDownloadEndHour))
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            performanceMode: try container.decodeIfPresent(DownloadPerformanceMode.self, forKey: .performanceMode) ?? .balanced,
            directDownloadAccelerationEnabled: try container.decodeIfPresent(Bool.self, forKey: .directDownloadAccelerationEnabled) ?? true,
            directDownloadMaxConnections: try container.decodeIfPresent(Int.self, forKey: .directDownloadMaxConnections) ?? 4,
            directDownloadSegmentSizeBytes: try container.decodeIfPresent(Int.self, forKey: .directDownloadSegmentSizeBytes) ?? 4 * 1024 * 1024,
            simultaneousDownloadJobs: try container.decodeIfPresent(Int.self, forKey: .simultaneousDownloadJobs) ?? 2,
            ytdlpConcurrentFragments: try container.decodeIfPresent(Int.self, forKey: .ytdlpConcurrentFragments) ?? 4,
            ffmpegThreadCount: try container.decodeIfPresent(Int.self, forKey: .ffmpegThreadCount) ?? 0,
            downloadRateLimit: try container.decodeIfPresent(String.self, forKey: .downloadRateLimit) ?? "",
            nightDownloadEnabled: try container.decodeIfPresent(Bool.self, forKey: .nightDownloadEnabled) ?? false,
            nightDownloadStartHour: try container.decodeIfPresent(Int.self, forKey: .nightDownloadStartHour) ?? 23,
            nightDownloadEndHour: try container.decodeIfPresent(Int.self, forKey: .nightDownloadEndHour) ?? 7
        )
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(performanceMode, forKey: .performanceMode)
        try container.encode(directDownloadAccelerationEnabled, forKey: .directDownloadAccelerationEnabled)
        try container.encode(directDownloadMaxConnections, forKey: .directDownloadMaxConnections)
        try container.encode(directDownloadSegmentSizeBytes, forKey: .directDownloadSegmentSizeBytes)
        try container.encode(simultaneousDownloadJobs, forKey: .simultaneousDownloadJobs)
        try container.encode(ytdlpConcurrentFragments, forKey: .ytdlpConcurrentFragments)
        try container.encode(ffmpegThreadCount, forKey: .ffmpegThreadCount)
        try container.encode(downloadRateLimit, forKey: .downloadRateLimit)
        try container.encode(nightDownloadEnabled, forKey: .nightDownloadEnabled)
        try container.encode(nightDownloadStartHour, forKey: .nightDownloadStartHour)
        try container.encode(nightDownloadEndHour, forKey: .nightDownloadEndHour)
    }

    public var directDownloadSegmentMinBytes: Int {
        max(8 * 1024 * 1024, directDownloadSegmentSizeBytes * 2)
    }

    public var directDownloadMaxConnectionsForBackend: Int {
        directDownloadAccelerationEnabled ? directDownloadMaxConnections : 1
    }
}

public struct AppSettings: Codable, Sendable, Equatable {
    private enum CodingKeys: String, CodingKey {
        case apiBaseURL
        case autoPasteEnabled
        case preferredQuality
        case localBackendSecret
        case bootstrapCode
        case autoSaveCompletedArtifactsToPhotos
        case downloadPerformance
    }

    public var apiBaseURL: URL
    public var autoPasteEnabled: Bool
    public var preferredQuality: String?
    public var localBackendSecret: String
    public var bootstrapCode: String?
    public var autoSaveCompletedArtifactsToPhotos: Bool
    public var downloadPerformance: DownloadPerformanceSettings

    public init(
        apiBaseURL: URL = URL(string: "http://127.0.0.1:8000")!,
        autoPasteEnabled: Bool = true,
        preferredQuality: String? = nil,
        localBackendSecret: String = "",
        bootstrapCode: String? = nil,
        autoSaveCompletedArtifactsToPhotos: Bool = false,
        downloadPerformance: DownloadPerformanceSettings = .balanced
    ) {
        self.apiBaseURL = apiBaseURL
        self.autoPasteEnabled = autoPasteEnabled
        self.preferredQuality = preferredQuality
        self.localBackendSecret = localBackendSecret
        self.bootstrapCode = bootstrapCode
        self.autoSaveCompletedArtifactsToPhotos = autoSaveCompletedArtifactsToPhotos
        self.downloadPerformance = downloadPerformance
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.apiBaseURL = try container.decode(URL.self, forKey: .apiBaseURL)
        self.autoPasteEnabled = try container.decodeIfPresent(Bool.self, forKey: .autoPasteEnabled) ?? true
        self.preferredQuality = try container.decodeIfPresent(String.self, forKey: .preferredQuality)
        self.localBackendSecret = try container.decodeIfPresent(String.self, forKey: .localBackendSecret) ?? ""
        self.bootstrapCode = try container.decodeIfPresent(String.self, forKey: .bootstrapCode)
        self.autoSaveCompletedArtifactsToPhotos = try container.decodeIfPresent(Bool.self, forKey: .autoSaveCompletedArtifactsToPhotos) ?? false
        self.downloadPerformance = try container.decodeIfPresent(DownloadPerformanceSettings.self, forKey: .downloadPerformance) ?? .balanced
    }
}
