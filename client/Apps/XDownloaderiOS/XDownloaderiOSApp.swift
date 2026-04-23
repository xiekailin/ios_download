import AppCore
import Networking
import SharedUI
import Storage
import SwiftUI

@main
struct XDownloaderiOSApp: App {
    @State private var store = JobStore()
    private let controller = AppController(
        apiClient: APIClient(baseURL: AppSettings().apiBaseURL),
        registrationStore: LocalRegistrationRepository(),
        jobsStore: LocalMediaRepository(),
        deviceName: "iPhone",
        platform: "ios",
        appVersion: "0.1.0"
    )

    var body: some Scene {
        WindowGroup {
            JobListScreen(title: "视频下载", store: store, controller: controller)
                .task { await controller.start(store: store) }
        }
    }
}

private struct JobListScreen: View {
    let title: String
    @Bindable var store: JobStore
    let controller: AppController
    @State private var pendingDeleteJob: Job?

    var body: some View {
        NavigationStack {
            List {
                Section("新任务") {
                    Text("首次创建任务时会自动初始化当前设备。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    TextField("粘贴分享链接", text: $store.draftURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    HStack {
                        Button("创建任务") {
                            Task { await controller.submitCurrentURL(store: store) }
                        }
                        .buttonStyle(.borderedProminent)
                        Button("刷新") {
                            Task { await controller.refreshJobs(store: store) }
                        }
                        .buttonStyle(.bordered)
                    }
                    .disabled(store.isLoading)
                }
                Section("任务") {
                    if store.jobs.isEmpty {
                        Text("还没有任务")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(store.jobs) { job in
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text(job.mediaTitle ?? job.sourceURL)
                                    .font(.headline)
                                    .lineLimit(2)
                                Spacer()
                                TaskStatusBadge(status: job.status)
                            }
                            DownloadProgressDetails(job: job)
                        }
                        .padding(.vertical, 4)
                        .swipeActions {
                            if job.status.isTerminal {
                                Button("删除记录", role: .destructive) {
                                    pendingDeleteJob = job
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle(title)
            .confirmationDialog(
                "删除这条下载记录？",
                isPresented: Binding(
                    get: { pendingDeleteJob != nil },
                    set: { isPresented in
                        if !isPresented {
                            pendingDeleteJob = nil
                        }
                    }
                ),
                titleVisibility: .visible
            ) {
                Button("删除记录", role: .destructive) {
                    guard let pendingDeleteJob else { return }
                    Task { await controller.deleteJob(id: pendingDeleteJob.id, store: store) }
                    self.pendingDeleteJob = nil
                }
                Button("取消", role: .cancel) {
                    pendingDeleteJob = nil
                }
            } message: {
                Text("删除后这条任务记录会从列表移除。")
            }
            .overlay(alignment: .bottom) {
                if let errorMessage = store.errorMessage {
                    Text(errorMessage)
                        .font(.footnote)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(.red.opacity(0.9), in: Capsule())
                        .padding()
                }
            }
        }
    }
}
