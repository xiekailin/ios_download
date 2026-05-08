import os

code = """import AppCore
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
                    .padding(.vertical, 4)
                    .tag(job.id)
                    .contextMenu {
                        if job.status.isTerminal {
                            Button("删除记录") {
                                pendingDeleteJob = job
                            }
                        }
                    }
                }
                .navigationTitle("任务列表")
            } detail: {
                ScrollView {
                    VStack(alignment: .leading, spacing: 24) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("创建新任务")
                                .font(.system(size: 28, weight: .bold))
                            Text("粘贴视频分享链接即可开始下载。")
                                .font(.body)
                                .foregroundStyle(.secondary)
                        }
                        .padding(.bottom, 8)

                        // 提交流程面板
                        VStack(alignment: .leading, spacing: 16) {
                            VStack(alignment: .leading, spacing: 8) {
                                Text("分享链接")
                                    .font(.headline)
                                    .foregroundStyle(.secondary)
                                TextField("粘贴包含分享链接的文本", text: $store.draftURL)
                                    .textFieldStyle(.roundedBorder)
                                    .controlSize(.large)
                                    .font(.body)
                                
                                HStack(spacing: 12) {
                                    Button(action: {
                                        Task { await controller.submitCurrentURL(store: store) }
                                    }) {
                                        HStack {
                                            Image(systemName: "arrow.down.circle.fill")
                                            Text(store.isLoading ? "正在创建…" : "创建任务")
                                        }
                                        .frame(minWidth: 100)
                                    }
                                    .buttonStyle(.borderedProminent)
                                    .controlSize(.large)
                                    .disabled(store.isLoading)
                                    
                                    Button("刷新列表") {
                                        Task { await controller.refreshJobs(store: store) }
                                    }
                                    .controlSize(.large)
                                    .disabled(store.isLoading)
                                }
                            }
                        }
                        .padding(20)
                        .background(Color(NSColor.controlBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        .shadow(color: Color.black.opacity(0.05), radius: 4, x: 0, y: 2)
                        .overlay(
                            RoundedRectangle(cornerRadius: 12)
                                .stroke(Color.secondary.opacity(0.1), lineWidth: 1)
                        )

                        // 详情面板
                        if let job = selectedJob {
                            VStack(alignment: .leading, spacing: 20) {
                                HStack(alignment: .top) {
                                    VStack(alignment: .leading, spacing: 8) {
                                        Text(job.mediaTitle ?? job.sourceURL)
                                            .font(.title2.bold())
                                            .lineLimit(2)
                                        
                                        if let author = job.authorHandle {
                                            Text(author)
                                                .font(.subheadline)
                                                .foregroundStyle(.secondary)
                                        }
                                    }
                                    Spacer()
                                    TaskStatusBadge(status: job.status)
                                        .scaleEffect(1.1)
                                }
                                
                                Divider()
                                
                                DownloadProgressDetails(job: job)
                                
                                if job.status.isTerminal {
                                    HStack {
                                        Button(role: .destructive, action: {
                                            pendingDeleteJob = job
                                        }) {
                                            HStack {
                                                Image(systemName: "trash")
                                                Text("删除历史记录")
                                            }
                                        }
                                        .controlSize(.large)
                                        .disabled(store.isLoading)
                                    }
                                    .padding(.top, 8)
                                }
                            }
                            .padding(20)
                            .background(Color(NSColor.controlBackgroundColor))
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                            .shadow(color: Color.black.opacity(0.05), radius: 4, x: 0, y: 2)
                            .overlay(
                                RoundedRectangle(cornerRadius: 12)
                                    .stroke(Color.secondary.opacity(0.1), lineWidth: 1)
                            )
                        } else {
                            VStack(alignment: .center, spacing: 16) {
                                Image(systemName: "doc.text.magnifyingglass")
                                    .font(.system(size: 48))
                                    .foregroundStyle(.tertiary)
                                Text("选择左侧任务查看详情")
                                    .font(.title3.bold())
                                    .foregroundStyle(.secondary)
                            }
                            .frame(maxWidth: .infinity, minHeight: 200)
                            .padding(20)
                            .background(Color(NSColor.controlBackgroundColor))
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                            .overlay(
                                RoundedRectangle(cornerRadius: 12)
                                    .stroke(Color.secondary.opacity(0.1), lineWidth: 1)
                            )
                        }
                    }
                    .frame(maxWidth: 800, alignment: .leading)
                    .padding(32)
                    .padding(.bottom, 56)
                }
                .background(Color(NSColor.windowBackgroundColor))
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
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .background(.red.opacity(0.9), in: Capsule())
                        .shadow(color: .black.opacity(0.1), radius: 4, y: 2)
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
"""

with open("client/Apps/XDownloaderMac/XDownloaderMacApp.swift", "w") as f:
    f.write(code)

print("Generated clean Mac app with original logic + new design")
