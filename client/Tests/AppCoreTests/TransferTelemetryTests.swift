import AppCore
import Foundation
import Testing

private func makeTelemetryJob(
    status: JobStatus,
    speedBytesPerSec: Int? = nil,
    progress: Int = 0
) -> Job {
    let now = Date()
    return Job(
        id: "job-telemetry",
        deviceID: "device-1",
        sourceURL: "https://x.com/demo/status/1",
        normalizedURL: "https://x.com/demo/status/1",
        provider: nil,
        jobType: .download,
        status: status,
        progress: progress,
        speedBytesPerSec: speedBytesPerSec,
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
        finishedAt: status.isTerminal ? now : nil
    )
}

@Test func transferTelemetryRecordsAndCapsSamples() {
    var telemetry = TransferTelemetry(maxSamples: 4)
    for index in 0..<6 {
        telemetry = telemetry.recording(
            job: makeTelemetryJob(status: .downloading, speedBytesPerSec: (index + 1) * 100),
            now: Date(timeIntervalSince1970: Double(index))
        )
    }

    #expect(telemetry.samples.count == 4)
    #expect(telemetry.samples.first?.bytesPerSecond == 300)
    #expect(telemetry.averageSpeedBytesPerSec == 450)
    #expect(telemetry.peakSpeedBytesPerSec == 600)
}

@Test func transferTelemetryClassifiesLocalProcessing() {
    let telemetry = TransferTelemetry()

    #expect(telemetry.bottleneck(for: makeTelemetryJob(status: .muxing)) == .localProcessing)
    #expect(telemetry.bottleneck(for: makeTelemetryJob(status: .paused)) == .waiting)
    #expect(telemetry.bottleneck(for: makeTelemetryJob(status: .completed)) == .completed)
}

@Test func transferTelemetryClassifiesLikelyPlatformLimitAfterSpeedDrops() {
    var telemetry = TransferTelemetry(maxSamples: 8)
    for (index, speed) in [2_000_000, 1_800_000, 70_000, 80_000, 75_000, 72_000].enumerated() {
        telemetry = telemetry.recording(
            job: makeTelemetryJob(status: .downloading, speedBytesPerSec: speed),
            now: Date(timeIntervalSince1970: Double(index))
        )
    }

    #expect(telemetry.bottleneck(for: makeTelemetryJob(status: .downloading, speedBytesPerSec: 72_000)) == .platformLimit)
}
