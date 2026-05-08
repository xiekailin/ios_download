import Foundation
import Observation

@MainActor
@Observable
public final class JobStore {
    private static let localJobPreservationInterval: TimeInterval = 10
    private var localInsertionDates: [Job.ID: Date] = [:]
    public private(set) var jobs: [Job] = []
    public private(set) var registration: DeviceRegistration?
    public var settings: AppSettings
    public var draftURL: String = ""
    public var batchDraftText: String = ""
    public var isLoading = false
    public var errorMessage: String?
    public private(set) var backendHealthStatus: BackendHealthStatus = .unknown
    public private(set) var youtubeCookieStatus: YouTubeCookieStatus?
    public var isPolling = false
    var lastAppliedClipboardText: String?

    public init(settings: AppSettings = AppSettings(), registration: DeviceRegistration? = nil) {
        self.settings = settings
        self.registration = registration
    }

    public func replaceJobs(_ jobs: [Job]) {
        localInsertionDates.removeAll()
        self.jobs = jobs.sorted { $0.createdAt > $1.createdAt }
    }

    public func replaceJobsPreservingActiveLocalJobs(_ remoteJobs: [Job], now: Date = Date()) {
        let remoteIDs = Set(remoteJobs.map(\.id))
        let localOnlyActiveJobs = jobs.filter { job in
            guard let insertedAt = localInsertionDates[job.id] else { return false }
            return !remoteIDs.contains(job.id)
                && !job.status.isTerminal
                && now.timeIntervalSince(insertedAt) < Self.localJobPreservationInterval
        }
        let preservedInsertionDates = Dictionary(
            uniqueKeysWithValues: localOnlyActiveJobs.compactMap { job in
                localInsertionDates[job.id].map { (job.id, $0) }
            }
        )
        self.jobs = (remoteJobs + localOnlyActiveJobs).sorted { $0.createdAt > $1.createdAt }
        localInsertionDates = preservedInsertionDates
    }

    public func upsert(_ job: Job, now: Date = Date()) {
        if let index = jobs.firstIndex(where: { $0.id == job.id }) {
            jobs[index] = job
        } else {
            localInsertionDates[job.id] = now
            jobs.insert(job, at: 0)
        }
        jobs.sort { $0.createdAt > $1.createdAt }
    }

    public func job(id: String) -> Job? {
        jobs.first(where: { $0.id == id })
    }

    public func remove(jobID: String) {
        jobs.removeAll { $0.id == jobID }
    }

    public func setRegistration(_ registration: DeviceRegistration?) {
        self.registration = registration
    }

    public func setSettings(_ settings: AppSettings) {
        self.settings = settings
    }

    public func setLoading(_ value: Bool) {
        isLoading = value
    }

    public func setPolling(_ value: Bool) {
        isPolling = value
    }

    public func setBackendHealthStatus(_ status: BackendHealthStatus) {
        backendHealthStatus = status
    }

    public func setYouTubeCookieStatus(_ status: YouTubeCookieStatus?) {
        youtubeCookieStatus = status
    }

    public func clearDraftURL() {
        draftURL = ""
    }

    public func clearBatchDraftText() {
        batchDraftText = ""
    }

    public var hasActiveJobs: Bool {
        jobs.contains { !$0.status.isTerminal }
    }

    public func setError(_ message: String?) {
        errorMessage = message
    }
}
