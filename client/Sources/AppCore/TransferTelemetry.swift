import Foundation

public struct TransferSpeedSample: Codable, Sendable, Equatable, Identifiable {
    public var id: Date { capturedAt }
    public let capturedAt: Date
    public let bytesPerSecond: Int
    public let progress: Int

    public init(capturedAt: Date, bytesPerSecond: Int, progress: Int) {
        self.capturedAt = capturedAt
        self.bytesPerSecond = max(0, bytesPerSecond)
        self.progress = max(0, min(progress, 100))
    }
}

public enum TransferBottleneck: Sendable, Equatable {
    case waiting
    case network
    case platformLimit
    case localProcessing
    case completed
    case failed

    public var title: String {
        switch self {
        case .waiting:
            "等待调度"
        case .network:
            "网络传输"
        case .platformLimit:
            "疑似平台限速"
        case .localProcessing:
            "本机处理中"
        case .completed:
            "已完成"
        case .failed:
            "需要处理"
        }
    }

    public var systemImage: String {
        switch self {
        case .waiting:
            "clock"
        case .network:
            "network"
        case .platformLimit:
            "speedometer"
        case .localProcessing:
            "cpu"
        case .completed:
            "checkmark.circle"
        case .failed:
            "exclamationmark.triangle"
        }
    }
}

public struct TransferTelemetry: Codable, Sendable, Equatable {
    public let samples: [TransferSpeedSample]
    public let maxSamples: Int

    public init(samples: [TransferSpeedSample] = [], maxSamples: Int = 36) {
        self.maxSamples = max(4, maxSamples)
        self.samples = Array(samples.suffix(self.maxSamples))
    }

    public func recording(job: Job, now: Date = Date()) -> TransferTelemetry {
        guard !job.status.isTerminal else { return self }
        let sample = TransferSpeedSample(
            capturedAt: now,
            bytesPerSecond: job.speedBytesPerSec ?? 0,
            progress: job.progress
        )
        return TransferTelemetry(samples: samples + [sample], maxSamples: maxSamples)
    }

    public var averageSpeedBytesPerSec: Int? {
        let positiveSamples = samples.map(\.bytesPerSecond).filter { $0 > 0 }
        guard !positiveSamples.isEmpty else { return nil }
        return positiveSamples.reduce(0, +) / positiveSamples.count
    }

    public var peakSpeedBytesPerSec: Int? {
        let peak = samples.map(\.bytesPerSecond).max() ?? 0
        return peak > 0 ? peak : nil
    }

    public var averageSpeedText: String {
        guard let averageSpeedBytesPerSec else { return "0 KB/s" }
        return Self.formatByteRate(averageSpeedBytesPerSec)
    }

    public var peakSpeedText: String {
        guard let peakSpeedBytesPerSec else { return "0 KB/s" }
        return Self.formatByteRate(peakSpeedBytesPerSec)
    }

    public func bottleneck(for job: Job) -> TransferBottleneck {
        switch job.status {
        case .completed:
            return .completed
        case .failed, .canceled:
            return .failed
        case .muxing, .storing:
            return .localProcessing
        case .created, .queued, .paused, .resolving, .resolved:
            return .waiting
        case .downloading:
            if isLikelyPlatformLimited {
                return .platformLimit
            }
            return .network
        }
    }

    private var isLikelyPlatformLimited: Bool {
        guard let peakSpeedBytesPerSec, peakSpeedBytesPerSec >= 256 * 1024 else { return false }
        let recentSamples = samples.suffix(4).map(\.bytesPerSecond).filter { $0 > 0 }
        guard recentSamples.count >= 3 else { return false }
        let recentAverage = recentSamples.reduce(0, +) / recentSamples.count
        return recentAverage < max(96 * 1024, peakSpeedBytesPerSec / 5)
    }

    public static func formatByteRate(_ value: Int) -> String {
        let formatter = ByteCountFormatter()
        formatter.allowedUnits = [.useKB, .useMB, .useGB]
        formatter.countStyle = .file
        formatter.includesUnit = true
        formatter.isAdaptive = true
        return "\(formatter.string(fromByteCount: Int64(max(0, value))))/s"
    }
}
