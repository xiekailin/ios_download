import AppCore
import Networking
import SharedUI
import Storage
import SwiftUI

@main
struct XDownloaderMacApp: App {
    @State private var store = JobStore()
    @State private var selectedJobID: Job.ID?
    @State private var pendingDeleteJob: Job?
    private let controller = AppController(
        apiClient: APIClient(baseURL: AppSettings().apiBaseURL),
        registrationStore: LocalRegistrationRepository(),
        jobsStore: LocalMediaRepository(),
        deviceName: "Mac",
        platform: "macos",
        appVersion: "0.1.0"
    )

    private var selectedJob: Job? {
        if let selectedJobID, let selectedJob = store.jobs.first(where: { $0.id == selectedJobID }) {
            return selectedJob
        }
        return store.jobs.first
    }

    var body: some Scene {
        WindowGroup {
            NavigationSplitView {
                List(store.jobs, selection: $selectedJobID) { job in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(job.mediaTitle ?? job.sourceURL)
                            .font(.headline)
                            .lineLimit(2)
                        Text(job.authorHandle ?? job.sourceURL)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .tag(job.id)
                    .contextMenu {
                        if job.status.isTerminal {
                            Button("删除记录") {
                                pendingDeleteJob = job
                            }
                        }
                    }
                }
                .navigationTitle("任务")
            } detail: {
                VStack(alignment: .leading, spacing: 16) {
                    Text("视频下载")
                        .font(.largeTitle.bold())
                    Text("首次创建任务时会自动初始化这台 Mac。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    TextField("粘贴分享链接", text: $store.draftURL)
                    HStack {
                        Button("创建任务") {
                            Task { await controller.submitCurrentURL(store: store) }
                        }
                        .buttonStyle(.borderedProminent)
                        Button("刷新") {
                            Task { await controller.refreshJobs(store: store) }
                        }
                    }
                    .disabled(store.isLoading)
                    if let job = selectedJob {
                        TaskStatusBadge(status: job.status)
                        DownloadProgressDetails(job: job)
                        if job.status.isTerminal {
                            Button("删除记录", role: .destructive) {
                                pendingDeleteJob = job
                            }
                        }
                    } else {
                        Text("暂无任务")
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                .padding(24)
            }
            .frame(minWidth: 900, minHeight: 560)
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
                    let deletedJobID = pendingDeleteJob.id
                    Task {
                        await controller.deleteJob(id: deletedJobID, store: store)
                        if selectedJobID == deletedJobID {
                            selectedJobID = store.jobs.first?.id
                        }
                    }
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
            .task { await controller.start(store: store) }
            .onChange(of: store.jobs) { _, jobs in
                if let selectedJobID, jobs.contains(where: { $0.id == selectedJobID }) {
                    return
                }
                selectedJobID = jobs.first?.id
            }
        }
    }
}
