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
}

public enum JobType: String, Codable, Sendable, CaseIterable {
    case download
    case audioDownload = "audio_download"
    case audioSeparation = "audio_separation"
}

public enum ArtifactRole: String, Codable, Sendable, CaseIterable {
    case media
    case vocals
    case accompaniment
}

public enum MaterialLibraryFilter: String, Codable, Sendable, CaseIterable, Identifiable {
    case all
    case video
    case audio
    case separated
    case attention

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .all:
            "全部"
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

    public var secondaryStatusText: String {
        if let displayErrorText {
            return displayErrorText
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

public struct AppSettings: Codable, Sendable, Equatable {
    private enum CodingKeys: String, CodingKey {
        case apiBaseURL
        case autoPasteEnabled
        case preferredQuality
        case localBackendSecret
        case bootstrapCode
        case autoSaveCompletedArtifactsToPhotos
    }

    public var apiBaseURL: URL
    public var autoPasteEnabled: Bool
    public var preferredQuality: String?
    public var localBackendSecret: String
    public var bootstrapCode: String?
    public var autoSaveCompletedArtifactsToPhotos: Bool

    public init(
        apiBaseURL: URL = URL(string: "http://127.0.0.1:8000")!,
        autoPasteEnabled: Bool = true,
        preferredQuality: String? = nil,
        localBackendSecret: String = "",
        bootstrapCode: String? = nil,
        autoSaveCompletedArtifactsToPhotos: Bool = false
    ) {
        self.apiBaseURL = apiBaseURL
        self.autoPasteEnabled = autoPasteEnabled
        self.preferredQuality = preferredQuality
        self.localBackendSecret = localBackendSecret
        self.bootstrapCode = bootstrapCode
        self.autoSaveCompletedArtifactsToPhotos = autoSaveCompletedArtifactsToPhotos
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.apiBaseURL = try container.decode(URL.self, forKey: .apiBaseURL)
        self.autoPasteEnabled = try container.decodeIfPresent(Bool.self, forKey: .autoPasteEnabled) ?? true
        self.preferredQuality = try container.decodeIfPresent(String.self, forKey: .preferredQuality)
        self.localBackendSecret = try container.decodeIfPresent(String.self, forKey: .localBackendSecret) ?? ""
        self.bootstrapCode = try container.decodeIfPresent(String.self, forKey: .bootstrapCode)
        self.autoSaveCompletedArtifactsToPhotos = try container.decodeIfPresent(Bool.self, forKey: .autoSaveCompletedArtifactsToPhotos) ?? false
    }
}
