import AppCore
import Testing
import Foundation

private func makeJob(id: String, sourceURL: String = "https://x.com/demo/status/1", now: Date) -> Job {
    Job(
        id: id,
        deviceID: "device-1",
        sourceURL: sourceURL,
        normalizedURL: sourceURL,
        provider: nil,
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

@Test func jobStoreUpsertReplacesExistingJob() async throws {
    let store = await MainActor.run { JobStore() }
    let now = Date()
    let first = makeJob(id: "job-1", now: now)
    let second = Job(
        id: "job-1",
        deviceID: "device-1",
        sourceURL: "https://x.com/demo/status/1",
        normalizedURL: "https://x.com/demo/status/1",
        provider: "yt-dlp",
        status: .completed,
        progress: 100,
        downloadedBytes: 1024,
        totalBytes: 2048,
        speedBytesPerSec: 512,
        etaSeconds: 2,
        errorCode: nil,
        errorMessage: nil,
        userMessage: nil,
        mediaTitle: "done",
        authorHandle: "demo",
        thumbnailURL: nil,
        artifactID: "artifact-1",
        selectedQuality: nil,
        createdAt: now,
        updatedAt: now,
        finishedAt: now
    )
    await MainActor.run {
        store.upsert(first)
        store.upsert(second)
        #expect(store.jobs.count == 1)
        #expect(store.jobs[0].status == .completed)
        #expect(store.jobs[0].artifactID == "artifact-1")
        #expect(store.jobs[0].downloadedBytes == 1024)
        #expect(store.jobs[0].totalBytes == 2048)
        #expect(store.jobs[0].speedBytesPerSec == 512)
        #expect(store.jobs[0].etaSeconds == 2)
    }
}

@Test func jobStoreRemoveDeletesMatchingJob() async throws {
    let store = await MainActor.run { JobStore() }
    let now = Date()
    await MainActor.run {
        store.upsert(makeJob(id: "job-1", now: now))
        store.upsert(makeJob(id: "job-2", sourceURL: "https://www.douyin.com/video/123456", now: now.addingTimeInterval(1)))
        store.remove(jobID: "job-1")
        #expect(store.jobs.count == 1)
        #expect(store.jobs[0].id == "job-2")
    }
}
