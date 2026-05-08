import AppCore
import Foundation
import Testing

@Test func applyClipboardTextFillsDraftWhenEmpty() async {
    let store = await MainActor.run { JobStore() }

    let didApply = await AppController.applyClipboardText("复制 https://x.com/demo/status/1", to: store)

    await MainActor.run {
        #expect(didApply)
        #expect(store.draftURL == "https://x.com/demo/status/1")
    }
}

@Test func applyClipboardTextDoesNotOverwriteExistingDraft() async {
    let store = await MainActor.run { JobStore() }
    await MainActor.run { store.draftURL = "https://x.com/demo/status/existing" }

    let didApply = await AppController.applyClipboardText("https://x.com/demo/status/1", to: store)

    await MainActor.run {
        #expect(!didApply)
        #expect(store.draftURL == "https://x.com/demo/status/existing")
    }
}

@Test func applyClipboardTextDoesNotProcessSameClipboardTextTwice() async {
    let store = await MainActor.run { JobStore() }

    let firstApply = await AppController.applyClipboardText("https://x.com/demo/status/1", to: store)
    await MainActor.run { store.clearDraftURL() }
    let secondApply = await AppController.applyClipboardText("https://x.com/demo/status/1", to: store)

    await MainActor.run {
        #expect(firstApply)
        #expect(!secondApply)
        #expect(store.draftURL.isEmpty)
    }
}

@Test func applyClipboardTextIgnoresUnsupportedText() async {
    let store = await MainActor.run { JobStore() }

    let didApply = await AppController.applyClipboardText("https://example.com/demo", to: store)

    await MainActor.run {
        #expect(!didApply)
        #expect(store.draftURL.isEmpty)
    }
}

@Test func handleDownloadDeepLinkFillsDraftWithoutSubmitting() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()
    let url = URL(string: "xdownloader://download?url=https%3A%2F%2Fx.com%2Fdemo%2Fstatus%2F1")!

    let action = await controller.handleDeepLink(url, store: store)

    await MainActor.run {
        #expect(action == .download)
        #expect(store.jobs.isEmpty)
        #expect(store.draftURL == "https://x.com/demo/status/1")
        #expect(store.errorMessage == nil)
    }
    #expect(await apiClient.createJobCalls == 0)
    #expect(await apiClient.createAudioDownloadJobCalls == 0)
}

@Test func handleAudioDeepLinkFillsDraftWithoutSubmitting() async throws {
    let registration = DeviceRegistration(deviceID: "device-1", accessToken: "token-1")
    let apiClient = MockAPIClient(registration: registration, jobs: [makeJob()])
    let controller = await makeController(apiClient: apiClient)
    let store = await makeStore()
    let url = URL(string: "xdownloader://audio?url=https%3A%2F%2Fx.com%2Fdemo%2Fstatus%2F1")!

    let action = await controller.handleDeepLink(url, store: store)

    await MainActor.run {
        #expect(action == .audio)
        #expect(store.jobs.isEmpty)
        #expect(store.draftURL == "https://x.com/demo/status/1")
        #expect(store.errorMessage == nil)
    }
    #expect(await apiClient.createJobCalls == 0)
    #expect(await apiClient.createAudioDownloadJobCalls == 0)
}
