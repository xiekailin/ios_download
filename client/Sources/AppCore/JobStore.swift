import Foundation
import Observation

@MainActor
@Observable
public final class JobStore {
    public private(set) var jobs: [Job] = []
    public private(set) var registration: DeviceRegistration?
    public var settings: AppSettings
    public var draftURL: String = ""
    public var isLoading = false
    public var errorMessage: String?
    public var isPolling = false

    public init(settings: AppSettings = AppSettings(), registration: DeviceRegistration? = nil) {
        self.settings = settings
        self.registration = registration
    }

    public func replaceJobs(_ jobs: [Job]) {
        self.jobs = jobs.sorted { $0.createdAt > $1.createdAt }
    }

    public func upsert(_ job: Job) {
        if let index = jobs.firstIndex(where: { $0.id == job.id }) {
            jobs[index] = job
        } else {
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

    public func clearDraftURL() {
        draftURL = ""
    }

    public var hasActiveJobs: Bool {
        jobs.contains { !$0.status.isTerminal }
    }

    public func setError(_ message: String?) {
        errorMessage = message
    }
}
