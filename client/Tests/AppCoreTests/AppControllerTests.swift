import AppCore
import Foundation
import Testing

private actor MockRegistrationStore: RegistrationStore {
    var registration: DeviceRegistration?

    func loadRegistration() async throws -> DeviceRegistration? {
        registration
    }

    func saveRegistration(_ registration: DeviceRegistration) async throws {
        self.registration = registration
    }
}

private actor MockJobsStore: JobsStore {
    var jobs: [Job] = []

    func loadJobs() async throws -> [Job] {
        jobs
    }

    func saveJobs(_ jobs: [Job]) async throws {
        self.jobs = jobs
    }
}

private struct MockClientError: LocalizedError {
    let errorDescription: String?
}

private actor MockAPIClient: ClientAPI {
    var registration: DeviceRegistration
    var jobs: [Job]
    var createdJobs: [Job] = []
    var deletedJobIDs: [String] = []
    var registerCalls = 0
    var createJobCalls = 0
    var deleteJobError: Error?

    init(registration: DeviceRegistration, jobs: [Job], deleteJobError: Error? = nil) {
        self.registration = registration
        self.jobs = jobs
        self.deleteJobError = deleteJobError
    }

    func registerDevice(name: String, platform: String, appVersion: String) async throws -> DeviceRegistration {
        registerCalls += 1
        return registration
    }

    func createJob(url: String, preferredQuality: String?, token: String) async throws -> Job {
        createJobCalls += 1
        let job = jobs[0]
        createdJobs.append(job)
        return job
    }

    func listJobs(token: String) async throws -> [Job] {
        jobs
    }

    func deleteJob(id: String, token: String) async throws -> Job {
        deletedJobIDs.append(id)
        if let deleteJobError {
            throw deleteJobError
        }
        return jobs.first(where: { $0.id == id }) ?? jobs[0]
    }
}

private func makeJob(id: String = "job-1", now: Date = Date()) -> Job {
    Job(
        id: id,
        deviceID: "device-1",
        sourceURL: "https://x.com/demo/status/1",
        normalizedURL: "https://x.com/demo/status/1",
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

private func makeController(
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

private func makeStore() async -> JobStore {
    await MainActor.run {
        let store = JobStore()
        store.setSettings(AppSettings(apiBaseURL: URL(string: "http://127.0.0.1:8000")!, autoPasteEnabled: true, preferredQuality: "720p"))
        return store
    }
}

@Test func appControllerRegistersAndRefreshesJobs() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    try await controller.ensureRegistration(store: store)
    await MainActor.run {
        #expect(store.registration?.deviceID == "device-1")
    }
    await MainActor.run {
        store.draftURL = "https://x.com/demo/status/1"
    }
    await controller.submitCurrentURL(store: store)
    await MainActor.run {
        #expect(store.jobs.count == 1)
        #expect(store.draftURL.isEmpty)
    }
    await controller.refreshJobs(store: store)
    await MainActor.run {
        #expect(store.jobs.count == 1)
        #expect(store.jobs[0].id == "job-1")
    }
}

@Test func submitCurrentURLAcceptsBilibiliURL() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.draftURL = "https://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007.tianma.1-2-2.click"
    }
    await controller.submitCurrentURL(store: store)

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
    await controller.submitCurrentURL(store: store)

    await MainActor.run {
        #expect(store.jobs.count == 1)
        #expect(store.errorMessage == nil)
        #expect(store.draftURL.isEmpty)
    }
    #expect(await apiClient.createJobCalls == 1)
}

@Test func submitCurrentURLRegistersDeviceBeforeCreatingJob() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()

    await MainActor.run {
        store.draftURL = "https://x.com/demo/status/1"
    }
    await controller.submitCurrentURL(store: store)

    await MainActor.run {
        #expect(store.registration?.deviceID == "device-1")
        #expect(store.jobs.count == 1)
        #expect(store.draftURL.isEmpty)
        #expect(store.errorMessage == nil)
    }
    #expect(await apiClient.registerCalls == 1)
    #expect(await apiClient.createJobCalls == 1)
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

@Test func deleteJobRemovesLocalRecord() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let firstJob = makeJob(id: "job-1")
    let secondJob = Job(
        id: "job-2",
        deviceID: "device-1",
        sourceURL: "https://www.douyin.com/video/123456",
        normalizedURL: "https://www.douyin.com/video/123456",
        provider: nil,
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
