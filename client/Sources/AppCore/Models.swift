import Foundation

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

public struct Job: Codable, Identifiable, Sendable, Equatable {
    public let id: String
    public let deviceID: String
    public let sourceURL: String
    public let normalizedURL: String
    public let provider: String?
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

    public var secondaryStatusText: String {
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
        let items = [
            progressText,
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

public struct AppSettings: Codable, Sendable, Equatable {
    public var apiBaseURL: URL
    public var autoPasteEnabled: Bool
    public var preferredQuality: String?

    public init(
        apiBaseURL: URL = URL(string: "http://127.0.0.1:8000")!,
        autoPasteEnabled: Bool = true,
        preferredQuality: String? = nil
    ) {
        self.apiBaseURL = apiBaseURL
        self.autoPasteEnabled = autoPasteEnabled
        self.preferredQuality = preferredQuality
    }
}
