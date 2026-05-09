import AppCore
import AppKit
import Networking
import PlatformAdapters
import SharedUI
import Storage
import SwiftUI
import UniformTypeIdentifiers
import UserNotifications

private enum MacNativeStyle {
    static let cornerRadius: CGFloat = 8
    static let panelRadius: CGFloat = 10
    static let rowRadius: CGFloat = 8

    static var panelBackground: Color { Color(NSColor.controlBackgroundColor) }
    static var insetBackground: Color { Color(NSColor.textBackgroundColor) }
    static var subtleBackground: Color { Color(NSColor.quaternaryLabelColor).opacity(0.08) }
    static var border: Color { Color(NSColor.separatorColor).opacity(0.55) }

    static func statusColor(_ status: JobStatus) -> Color {
        switch status {
        case .completed:
            .green
        case .failed:
            .red
        case .canceled:
            .secondary
        case .downloading, .resolving, .resolved, .muxing, .storing:
            .accentColor
        case .created, .queued:
            .orange
        }
    }

    static func statusSymbol(_ status: JobStatus) -> String {
        status.presentationSystemImage
    }

    static func jobTypeSymbol(_ jobType: JobType) -> String {
        jobType.presentationSystemImage
    }
}

@main
struct XDownloaderMacApp: App {
    @State private var store = JobStore(settings: Self.localSettings)
    @State private var selectedJobID: Job.ID?
    @State private var pendingDeleteJob: Job?
    @State private var isClearHistoryConfirmationPresented = false
    @State private var isCreatePanelPresented = false
    @State private var jobSearchText = ""
    @State private var selectedMaterialFilter: MaterialLibraryFilter = .all
    @State private var currentPreview: JobPreview?
    @State private var previewRequestKey: String?
    @State private var selectedPreviewJobType: JobType = .download
    @State private var selectedJobArtifacts: [ArtifactSummary] = []
    @State private var artifactJobID: Job.ID?
    @State private var exportedArtifactURLs: [ArtifactSummary.ID: URL] = [:]
    @State private var artifactLoadErrorMessage: String?
    @State private var activeArtifactActionID: ArtifactSummary.ID?
    @State private var retryingJobID: Job.ID?
    @State private var notifiedCompletedJobIDs: Set<Job.ID> = []
    @State private var previousJobStatuses: [Job.ID: JobStatus] = [:]
    @State private var selectedJobLogs: [JobLogEvent] = []
    @State private var logsJobID: Job.ID?
    @State private var logsLoadErrorMessage: String?
    @State private var artifactActionMessage: String?
    @State private var isLogSectionExpanded = false
    private static let localSettings = makeLocalSettings()
    private static let localSecret = localSettings.localBackendSecret
    private let fileExporter = FileExportAdapter()
    private let controller = AppController(
        apiClient: APIClient(baseURL: Self.localSettings.apiBaseURL, localSecret: Self.localSecret),
        registrationStore: LocalRegistrationRepository(),
        jobsStore: LocalMediaRepository(),
        deviceName: "Mac",
        platform: "macos",
        appVersion: "0.1.0"
    )

    private var filteredJobs: [Job] {
        let query = jobSearchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return store.jobs.filter { job in
            guard selectedMaterialFilter.matches(job) else { return false }
            guard !query.isEmpty else { return true }
            return [
                job.mediaTitle,
                job.authorHandle,
                job.sourceURL,
                jobTypeTitle(job.jobType),
                jobStatusTitle(job.status),
            ]
            .compactMap { $0?.lowercased() }
            .contains { $0.contains(query) }
        }
    }

    private var selectedJob: Job? {
        if let selectedJobID, let selectedJob = store.jobs.first(where: { $0.id == selectedJobID }) {
            return selectedJob
        }
        return store.jobs.first
    }

    private var visibleSelectedJob: Job? {
        if let selectedJob, filteredJobs.contains(where: { $0.id == selectedJob.id }) {
            return selectedJob
        }
        return filteredJobs.first
    }

    private var completedJobs: [Job] {
        filteredJobs.filter { $0.status == .completed }
    }

    private var activeJobs: [Job] {
        filteredJobs.filter { !$0.status.isTerminal }
    }

    private var attentionJobs: [Job] {
        filteredJobs.filter { $0.status == .failed || $0.status == .canceled }
    }

    private static func makeLocalSettings() -> AppSettings {
        let supportDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "XDownloader", directoryHint: .isDirectory)
        let secretURL = supportDirectory.appending(path: "local_backend_secret")
        if let secret = try? String(contentsOf: secretURL, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines), !secret.isEmpty {
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: secretURL.path)
            return AppSettings(apiBaseURL: URL(string: "http://127.0.0.1:18767")!, localBackendSecret: secret)
        }
        let secret = LocalBackendLauncher.makeLocalSecret()
        try? FileManager.default.createDirectory(at: supportDirectory, withIntermediateDirectories: true)
        try? secret.write(to: secretURL, atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: secretURL.path)
        return AppSettings(apiBaseURL: URL(string: "http://127.0.0.1:18767")!, localBackendSecret: secret)
    }

    private func selectAudioFileForSeparation() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = ["mp3", "wav", "m4a", "aac", "flac"].compactMap { UTType(filenameExtension: $0) }
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        if panel.runModal() == .OK, let url = panel.url {
            Task {
                if let job = await controller.submitAudioSeparation(fileURL: url, store: store) {
                    selectedJobID = job.id
                    isCreatePanelPresented = false
                }
            }
        }
    }

    private func startApp() async {
        await ensureBackendConnection(reportsErrors: true)
        await controller.start(store: store)
    }

    private func ensureBackendConnection(reportsErrors: Bool) async {
        let launcher = LocalBackendLauncher(localSecret: Self.localSecret)
        do {
            let status = try await launcher.startAndCheckHealth()
            store.setBackendHealthStatus(status)
            if status == .healthy {
                store.setError(nil)
            }
        } catch {
            store.setBackendHealthStatus(.unhealthy)
            if reportsErrors {
                store.setError(error.localizedDescription)
            }
        }
    }

    private func refreshBackendHealth(startIfNeeded: Bool = false) async {
        if startIfNeeded {
            await ensureBackendConnection(reportsErrors: true)
            return
        }
        let launcher = LocalBackendLauncher(localSecret: Self.localSecret)
        let status = await launcher.checkHealth()
        store.setBackendHealthStatus(status)
        if status == .healthy {
            store.setError(nil)
        }
    }

    private func requestNotificationAuthorization() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    private func updateCompletionNotifications(for jobs: [Job]) {
        if previousJobStatuses.isEmpty {
            previousJobStatuses = Dictionary(uniqueKeysWithValues: jobs.map { ($0.id, $0.status) })
            notifiedCompletedJobIDs.formUnion(jobs.filter { $0.status == .completed }.map(\.id))
            return
        }
        for job in jobs where job.status == .completed && !notifiedCompletedJobIDs.contains(job.id) {
            if previousJobStatuses[job.id] != .completed {
                sendCompletionNotification(for: job)
                notifiedCompletedJobIDs.insert(job.id)
            }
        }
        previousJobStatuses = Dictionary(uniqueKeysWithValues: jobs.map { ($0.id, $0.status) })
    }

    private func sendCompletionNotification(for job: Job) {
        let content = UNMutableNotificationContent()
        content.title = "素材已保存"
        content.body = "打开 XDownloader 查看和操作文件。"
        content.sound = .default
        let request = UNNotificationRequest(identifier: "xdl-completed-\(job.id)", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }

    private func refreshBackendHealthPeriodically() async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(10))
            await refreshBackendHealth(startIfNeeded: store.backendHealthStatus == .unhealthy)
        }
    }

    private func loadJobLogs(for job: Job?) async {
        let loadingJobID = job?.id
        guard let job else {
            selectedJobLogs = []
            logsLoadErrorMessage = nil
            logsJobID = nil
            return
        }
        guard let token = store.registration?.accessToken else {
            guard visibleSelectedJob?.id == loadingJobID else { return }
            selectedJobLogs = []
            logsLoadErrorMessage = "设备初始化失败，请刷新后重试。"
            logsJobID = loadingJobID
            return
        }
        logsJobID = nil
        logsLoadErrorMessage = nil
        do {
            let result = try await controller.listJobLogs(jobID: job.id, token: token)
            guard visibleSelectedJob?.id == job.id else { return }
            selectedJobLogs = result.items
            logsJobID = job.id
        } catch {
            guard visibleSelectedJob?.id == job.id else { return }
            selectedJobLogs = []
            logsLoadErrorMessage = error.localizedDescription
            logsJobID = job.id
        }
    }

    private func loadArtifacts(for job: Job?) async {
        let loadingJobID = job?.id
        artifactActionMessage = nil
        guard let job, job.status == .completed else {
            guard visibleSelectedJob?.id == loadingJobID else { return }
            selectedJobArtifacts = []
            artifactLoadErrorMessage = nil
            artifactJobID = loadingJobID
            return
        }
        guard let token = store.registration?.accessToken else {
            guard visibleSelectedJob?.id == loadingJobID else { return }
            selectedJobArtifacts = []
            artifactLoadErrorMessage = "设备初始化失败，请刷新后重试。"
            artifactJobID = loadingJobID
            return
        }
        artifactJobID = nil
        artifactLoadErrorMessage = nil
        do {
            let artifacts = try await controller.listJobArtifacts(jobID: job.id, token: token)
            guard visibleSelectedJob?.id == job.id else { return }
            selectedJobArtifacts = artifacts
            artifactJobID = job.id
        } catch {
            guard visibleSelectedJob?.id == job.id else { return }
            selectedJobArtifacts = []
            artifactLoadErrorMessage = error.localizedDescription
            artifactJobID = job.id
            store.setError(error.localizedDescription)
        }
    }

    private func localURL(for artifact: ArtifactSummary) -> URL? {
        if let url = exportedArtifactURLs[artifact.id], FileManager.default.fileExists(atPath: url.path) {
            return url
        }
        if let localPath = artifact.localPath, FileManager.default.fileExists(atPath: localPath) {
            let url = URL(fileURLWithPath: localPath)
            exportedArtifactURLs[artifact.id] = url
            return url
        }
        let candidates = fileExporter.existingExportCandidates(for: artifact.fileName, mimeType: artifact.mimeType, fileSize: artifact.fileSize)
        if candidates.count == 1, let url = candidates.first {
            exportedArtifactURLs[artifact.id] = url
            return url
        }
        if candidates.count > 1 {
            store.setError("找到多个同名文件，请先在 Finder 中确认要操作的文件。")
            return nil
        }
        store.setError("找不到本地文件，请刷新任务列表后重试。")
        return nil
    }

    private func copyArtifactFile(_ artifact: ArtifactSummary) {
        Task {
            activeArtifactActionID = artifact.id
            artifactActionMessage = nil
            defer { activeArtifactActionID = nil }
            guard let url = localURL(for: artifact) else { return }
            if fileExporter.copyFileToPasteboard(url) {
                artifactActionMessage = "已复制：\(artifact.fileName)"
            } else {
                store.setError("复制文件失败，请重试。")
            }
        }
    }

    private func revealArtifactInFinder(_ artifact: ArtifactSummary) {
        Task {
            activeArtifactActionID = artifact.id
            artifactActionMessage = nil
            defer { activeArtifactActionID = nil }
            guard let url = localURL(for: artifact) else { return }
            fileExporter.revealInFinder(url)
            artifactActionMessage = "已在 Finder 中定位：\(artifact.fileName)"
        }
    }

    private func openArtifactVideo(_ artifact: ArtifactSummary) {
        Task {
            activeArtifactActionID = artifact.id
            artifactActionMessage = nil
            defer { activeArtifactActionID = nil }
            guard let url = localURL(for: artifact) else { return }
            if fileExporter.openInDefaultApp(url) {
                artifactActionMessage = "已打开：\(artifact.fileName)"
            } else {
                store.setError("打开视频失败，请重试。")
            }
        }
    }

    private func copyArtifactPath(_ artifact: ArtifactSummary) {
        guard let url = localURL(for: artifact) else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(url.path, forType: .string)
        artifactActionMessage = "已复制路径：\(artifact.fileName)"
    }

    private func videoThumbnailImage(for artifact: ArtifactSummary) -> NSImage? {
        guard let thumbnailLocalPath = artifact.thumbnailLocalPath else { return nil }
        let url = URL(fileURLWithPath: thumbnailLocalPath)
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        return NSImage(contentsOf: url)
    }

    private func jobTypeTitle(_ jobType: JobType) -> String {
        jobType.presentationTitle
    }

    private func jobStatusTitle(_ status: JobStatus) -> String {
        status.presentationTitle
    }

    private func sidebarRow(_ job: Job) -> some View {
        HStack(alignment: .center, spacing: 9) {
            Image(systemName: MacNativeStyle.statusSymbol(job.status))
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(MacNativeStyle.statusColor(job.status))
                .frame(width: 18, height: 18)

            VStack(alignment: .leading, spacing: 3) {
                Text(job.mediaTitle ?? job.sourceURL)
                    .font(.callout.weight(.medium))
                    .lineLimit(1)
                    .foregroundStyle(.primary)

                HStack(spacing: 6) {
                    Image(systemName: MacNativeStyle.jobTypeSymbol(job.jobType))
                    Text(jobTypeTitle(job.jobType))
                    Text("·")
                    Text(jobStatusTitle(job.status))
                    if let speed = job.speedText, !job.status.isTerminal {
                        Text("·")
                        Text(speed)
                            .monospacedDigit()
                    }
                }
                .font(.caption2)
                .foregroundStyle(job.status == .failed ? .red : .secondary)

                if !job.status.isTerminal {
                    ProgressView(value: job.progressFraction, total: 1)
                        .controlSize(.small)
                        .tint(MacNativeStyle.statusColor(job.status))
                }
            }
            Spacer(minLength: 6)
            if !job.status.isTerminal {
                Text(job.progressText)
                    .font(.caption2.monospacedDigit().weight(.medium))
                    .foregroundStyle(.secondary)
                    .frame(width: 34, alignment: .trailing)
            }
        }
        .padding(.vertical, 3)
    }

    private var sidebarHeader: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("素材库")
                        .font(.headline.weight(.semibold))
                    Text("\(filteredJobs.count) 个项目")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    isCreatePanelPresented = true
                } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(.borderless)
                .accessibilityLabel("新建任务")
            }

            Picker("筛选", selection: $selectedMaterialFilter) {
                ForEach(MaterialLibraryFilter.allCases) { filter in
                    Text(filter.title).tag(filter)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 12)
        .padding(.top, 12)
        .padding(.bottom, 8)
    }

    private var sidebarFooter: some View {
        VStack(spacing: 8) {
            Divider()
            Button(role: .destructive) {
                isClearHistoryConfirmationPresented = true
            } label: {
                Label("清理全部历史", systemImage: "trash")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderless)
            .controlSize(.regular)
            .disabled(store.jobs.isEmpty || store.isLoading)
            .padding(.horizontal, 12)
            .padding(.bottom, 10)
        }
    }

    private func jobHeader(_ job: Job) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: MacNativeStyle.jobTypeSymbol(job.jobType))
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(MacNativeStyle.statusColor(job.status))
                .frame(width: 30, height: 30)
                .background(MacNativeStyle.statusColor(job.status).opacity(0.12), in: RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius))

            VStack(alignment: .leading, spacing: 6) {
                Text(job.mediaTitle ?? job.sourceURL)
                    .font(.title3.weight(.semibold))
                    .lineLimit(2)

                HStack(spacing: 8) {
                    Text(jobTypeTitle(job.jobType))
                    Text("·")
                    Text(jobStatusTitle(job.status))
                    if let author = job.authorHandle {
                        Text("·")
                        Text(author)
                    }
                    Text("·")
                    Text(job.sourceURL)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            TaskStatusBadge(status: job.status)
        }
    }

    private func inspectorSection<Content: View>(
        _ title: String,
        systemImage: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: systemImage)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.primary)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 12)
    }

    private func jobLogsSection(for job: Job) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Spacer()
                Button("刷新日志") {
                    Task { await loadJobLogs(for: job) }
                }
                .controlSize(.small)
                .disabled(logsJobID == nil)
            }
            if logsJobID != job.id {
                ProgressView("正在加载日志…")
                    .controlSize(.small)
            } else if let logsLoadErrorMessage {
                Text("日志加载失败：\(logsLoadErrorMessage)")
                    .font(.footnote)
                    .foregroundStyle(.red)
            } else if selectedJobLogs.isEmpty {
                Text("暂无日志，任务开始后会显示关键步骤。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(selectedJobLogs) { event in
                        HStack(alignment: .top, spacing: 10) {
                            Text(event.createdAtText)
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.tertiary)
                                .frame(width: 72, alignment: .leading)
                            Text(event.levelTitle)
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(event.level == "error" ? .red : .secondary)
                                .frame(width: 46, alignment: .leading)
                            Text(event.message)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("时间 \(event.createdAtText)，级别 \(event.levelTitle)，内容 \(event.message)")
                    }
                }
                .padding(10)
                .background(MacNativeStyle.insetBackground, in: RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius))
                .overlay(
                    RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius)
                        .stroke(MacNativeStyle.border, lineWidth: 1)
                )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func artifactActionsSection(for job: Job) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            if artifactJobID != job.id {
                ProgressView("正在加载文件…")
                    .controlSize(.small)
            } else if let artifactLoadErrorMessage {
                VStack(alignment: .leading, spacing: 8) {
                    Text("文件加载失败：\(artifactLoadErrorMessage)")
                        .font(.footnote)
                        .foregroundStyle(.red)
                    Button("重试") {
                        Task { await loadArtifacts(for: job) }
                    }
                }
            } else if selectedJobArtifacts.isEmpty {
                Text("暂无可操作文件。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(selectedJobArtifacts) { artifact in
                    artifactActionRow(artifact, for: job)
                }
                if let artifactActionMessage {
                    Text(artifactActionMessage)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func existingExportMessage(for artifact: ArtifactSummary) -> String? {
        guard artifact.localPath == nil else { return nil }
        let candidates = fileExporter.existingExportCandidates(for: artifact.fileName, mimeType: artifact.mimeType, fileSize: artifact.fileSize)
        guard !candidates.isEmpty else { return nil }
        return candidates.count == 1 ? "下载目录已有同名文件，将复用现有文件。" : "下载目录已有 \(candidates.count) 个同名文件，请在 Finder 中确认后操作。"
    }

    private func artifactActionButtons(_ artifact: ArtifactSummary, for job: Job, axis: Axis) -> some View {
        let layout = axis == .horizontal ? AnyLayout(HStackLayout(spacing: 8)) : AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
        return layout {
            if activeArtifactActionID == artifact.id {
                ProgressView()
                    .controlSize(.small)
                    .accessibilityLabel("正在处理文件")
            }
            if job.jobType == .download, artifact.isPlayableVideo {
                Button {
                    openArtifactVideo(artifact)
                } label: {
                    Label("播放", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .disabled(activeArtifactActionID != nil)
            }
            Button {
                copyArtifactFile(artifact)
            } label: {
                Label("复制文件", systemImage: "doc.on.doc")
            }
            .buttonStyle(.bordered)
            .disabled(activeArtifactActionID != nil)
            Button {
                revealArtifactInFinder(artifact)
            } label: {
                Label("Finder", systemImage: "folder")
            }
            .buttonStyle(.bordered)
            .disabled(activeArtifactActionID != nil)
            Button {
                copyArtifactPath(artifact)
            } label: {
                Label("复制路径", systemImage: "link")
            }
            .buttonStyle(.bordered)
            .disabled(activeArtifactActionID != nil)
        }
    }

    private func artifactActionRow(_ artifact: ArtifactSummary, for job: Job) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            if job.jobType == .download, artifact.isPlayableVideo, let image = videoThumbnailImage(for: artifact) {
                Image(nsImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(maxWidth: .infinity, maxHeight: 220, alignment: .leading)
                    .clipShape(RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius))
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(artifact.fileName)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(2)
                if let details = artifact.mediaDetailsText {
                    Text(details)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let message = existingExportMessage(for: artifact) {
                    Text(message)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), alignment: .leading)], alignment: .leading, spacing: 8) {
                ForEach(Array(artifact.detailItems.enumerated()), id: \.offset) { _, item in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.label)
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                        Text(item.value)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(item.label == "本地路径" ? 2 : 1)
                            .truncationMode(.middle)
                    }
                }
            }
            .padding(10)
            .background(MacNativeStyle.subtleBackground, in: RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius))

            ViewThatFits(in: .horizontal) {
                artifactActionButtons(artifact, for: job, axis: .horizontal)
                artifactActionButtons(artifact, for: job, axis: .vertical)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(MacNativeStyle.insetBackground, in: RoundedRectangle(cornerRadius: MacNativeStyle.panelRadius))
        .overlay(
            RoundedRectangle(cornerRadius: MacNativeStyle.panelRadius)
                .stroke(MacNativeStyle.border, lineWidth: 1)
        )
    }

    private func resetArtifactState() {
        selectedJobArtifacts = []
        artifactJobID = nil
        artifactLoadErrorMessage = nil
        activeArtifactActionID = nil
        artifactActionMessage = nil
    }

    private func resetPreviewState() {
        currentPreview = nil
        previewRequestKey = nil
        selectedPreviewJobType = .download
    }

    private func previewCurrentURL(jobType: JobType? = nil) {
        let requestedJobType = jobType ?? selectedPreviewJobType
        let rawURL = store.draftURL.trimmingCharacters(in: .whitespacesAndNewlines)
        let requestKey = "\(requestedJobType.rawValue):\(rawURL)"
        previewRequestKey = requestKey
        Task {
            if let preview = await controller.previewCurrentURL(store: store, jobType: requestedJobType), previewRequestKey == requestKey {
                currentPreview = preview
                if jobType == nil {
                    selectedPreviewJobType = preview.recommendedJobType
                }
            }
        }
    }

    private func submitPreviewSelection() {
        Task {
            let job: Job?
            switch selectedPreviewJobType {
            case .download:
                job = await controller.submitCurrentURL(store: store)
            case .audioDownload:
                job = await controller.submitAudioDownloadURL(store: store)
            case .audioSeparation:
                job = nil
                store.setError("人声/伴奏拆分请先选择本地音频文件。")
            }
            if let job {
                selectedJobID = job.id
                isCreatePanelPresented = false
                resetPreviewState()
            }
        }
    }

    private func openExistingPreviewFile() {
        guard let localPath = currentPreview?.existingLocalPath else { return }
        if !fileExporter.openInDefaultApp(URL(fileURLWithPath: localPath)) {
            store.setError("打开已有文件失败，请重试。")
        }
    }

    private func revealExistingPreviewFile() {
        guard let localPath = currentPreview?.existingLocalPath else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: localPath)])
    }

    private func clearAllHistory() {
        Task {
            let result = await controller.deleteHistory(store: store)
            if result != nil {
                selectedJobID = store.jobs.first?.id
                resetArtifactState()
            }
        }
    }

    private func terminalJobActions(for job: Job) -> some View {
        HStack(spacing: 8) {
            if job.status == .failed || job.status == .canceled {
                Button {
                    retryingJobID = job.id
                    resetArtifactState()
                    Task {
                        await controller.retryJob(id: job.id, store: store)
                        retryingJobID = nil
                    }
                } label: {
                    Label(retryingJobID == job.id ? "正在重试…" : "重试任务", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.borderedProminent)
                .disabled(store.isLoading || retryingJobID != nil)
            }

            Button(role: .destructive, action: {
                pendingDeleteJob = job
            }) {
                Label("删除历史记录", systemImage: "trash")
            }
            .disabled(store.isLoading)
        }
    }

    private var submitButtons: some View {
        VStack(alignment: .leading, spacing: 8) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    previewButton
                    directDownloadButton
                    localAudioButton
                }
                VStack(alignment: .leading, spacing: 8) {
                    previewButton
                    directDownloadButton
                    localAudioButton
                }
            }
        }
    }

    private var previewButton: some View {
        Button(action: { previewCurrentURL(jobType: .download) }) {
            Label(store.isLoading ? "正在预览…" : "预览链接", systemImage: "eye")
                .frame(minWidth: 100)
        }
        .buttonStyle(.borderedProminent)
        .disabled(store.isLoading || store.backendHealthStatus == .unhealthy)
    }

    private var directDownloadButton: some View {
        Button(action: submitPreviewSelection) {
            Label("直接下载", systemImage: "arrow.down.circle")
        }
        .buttonStyle(.bordered)
        .disabled(store.isLoading || store.backendHealthStatus == .unhealthy)
    }

    private var localAudioButton: some View {
        Button(action: selectAudioFileForSeparation) {
            Label("拆分本地音频", systemImage: "waveform")
        }
        .buttonStyle(.bordered)
        .disabled(store.isLoading || store.backendHealthStatus == .unhealthy)
    }

    private func createTaskPanel() -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label("添加素材", systemImage: "plus.circle")
                    .font(.title3.weight(.semibold))
                Spacer()
                if store.backendHealthStatus == .unhealthy {
                    Label("后端未连接", systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(.red)
                }
            }

            VStack(alignment: .leading, spacing: 8) {
                Text("分享链接")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                TextField("粘贴 X、YouTube、Bilibili 等分享链接", text: $store.draftURL)
                    .textFieldStyle(.roundedBorder)
                    .controlSize(.large)
                    .font(.body)
                    .accessibilityLabel("分享链接")
                    .accessibilityHint("粘贴分享链接后先预览内容，再选择保存视频或提取 MP3。")
                    .onChange(of: store.draftURL) { _, _ in
                        resetPreviewState()
                    }
            }

            submitButtons

            if let currentPreview {
                previewConfirmationCard(currentPreview)
            }

            if store.backendHealthStatus == .unhealthy {
                Text("下载服务没有启动，请先启动后端服务。")
                    .font(.footnote)
                    .foregroundStyle(.red)
            }

            HStack {
                Spacer()
                Button("关闭") {
                    isCreatePanelPresented = false
                }
                .keyboardShortcut(.cancelAction)
            }
        }
        .padding(22)
        .frame(width: 580, alignment: .leading)
        .background(MacNativeStyle.panelBackground)
    }

    private func previewConfirmationCard(_ preview: JobPreview) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                AsyncImage(url: preview.thumbnailURL.flatMap(URL.init(string:))) { image in
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                } placeholder: {
                    RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius)
                        .fill(Color.secondary.opacity(0.12))
                        .overlay(Image(systemName: "play.rectangle").foregroundStyle(.secondary))
                }
                .frame(width: 124, height: 76)
                .clipShape(RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius))

                VStack(alignment: .leading, spacing: 6) {
                    Text(preview.displayTitle)
                        .font(.headline)
                        .lineLimit(2)
                    if let author = preview.authorHandle {
                        Text(author)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    Text("来源：\(preview.provider) · \(preview.fileExtension.uppercased())")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Spacer()
            }

            Picker("保存方式", selection: $selectedPreviewJobType) {
                Label("保存视频", systemImage: "film").tag(JobType.download)
                Label("提取 MP3", systemImage: "music.note").tag(JobType.audioDownload)
            }
            .pickerStyle(.segmented)
            .onChange(of: selectedPreviewJobType) { _, newValue in
                previewCurrentURL(jobType: newValue)
            }

            if preview.canReuseExisting {
                VStack(alignment: .leading, spacing: 8) {
                    Label("这个素材已经下载过：\(preview.existingFileName ?? "已有文件")", systemImage: "checkmark.circle")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.secondary)
                    HStack(spacing: 8) {
                        Button(action: openExistingPreviewFile) {
                            Label("打开已有文件", systemImage: "play")
                        }
                        Button(action: revealExistingPreviewFile) {
                            Label("打开 Finder", systemImage: "folder")
                        }
                    }
                    .controlSize(.small)
                }
                .padding(10)
                .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius))
            }

            HStack(spacing: 8) {
                Button(action: submitPreviewSelection) {
                    Label(preview.canReuseExisting ? "重新下载为新版本" : "确认添加素材", systemImage: "checkmark.circle.fill")
                }
                .buttonStyle(.borderedProminent)
                .disabled(store.isLoading)

                Button("取消预览", action: resetPreviewState)
                    .buttonStyle(.bordered)
            }
        }
        .padding(12)
        .background(MacNativeStyle.insetBackground, in: RoundedRectangle(cornerRadius: MacNativeStyle.panelRadius))
        .overlay(
            RoundedRectangle(cornerRadius: MacNativeStyle.panelRadius)
                .stroke(MacNativeStyle.border, lineWidth: 1)
        )
    }

    private var emptyHistoryPanel: some View {
        VStack(spacing: 14) {
            ContentUnavailableView(
                store.jobs.isEmpty ? "还没有保存素材" : "没有匹配的素材",
                systemImage: store.jobs.isEmpty ? "tray" : "line.3.horizontal.decrease.circle",
                description: Text(store.jobs.isEmpty ? "添加第一个下载任务后，文件和日志会显示在这里。" : "换一个关键词或筛选条件。")
            )
            if store.jobs.isEmpty {
                Button {
                    isCreatePanelPresented = true
                } label: {
                    Label("新建第一个任务", systemImage: "plus")
                }
                .buttonStyle(.borderedProminent)
            } else {
                Button(jobSearchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "重置筛选" : "清空搜索和筛选") {
                    jobSearchText = ""
                    selectedMaterialFilter = .all
                }
                .buttonStyle(.bordered)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 320)
        .padding(24)
    }

    private func jobDetailPanel(_ job: Job) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            jobHeader(job)
                .padding(.bottom, 14)

            Divider()

            inspectorSection("任务进度", systemImage: "gauge.with.dots.needle.bottom.50percent") {
                DownloadProgressDetails(job: job)
            }

            if job.status == .completed {
                Divider()
                inspectorSection("文件", systemImage: "doc") {
                    artifactActionsSection(for: job)
                }
            }

            Divider()
            DisclosureGroup(isExpanded: $isLogSectionExpanded) {
                jobLogsSection(for: job)
                    .padding(.top, 8)
            } label: {
                Label("日志", systemImage: "list.bullet.rectangle")
                    .font(.subheadline.weight(.semibold))
            }
            .padding(.vertical, 12)

            if job.status.isTerminal {
                Divider()
                inspectorSection("操作", systemImage: "slider.horizontal.3") {
                    terminalJobActions(for: job)
                }
            }
        }
        .padding(18)
        .background(MacNativeStyle.panelBackground, in: RoundedRectangle(cornerRadius: MacNativeStyle.panelRadius))
        .overlay(
            RoundedRectangle(cornerRadius: MacNativeStyle.panelRadius)
                .stroke(MacNativeStyle.border, lineWidth: 1)
        )
    }

    var body: some Scene {
        WindowGroup {
            NavigationSplitView {
                VStack(spacing: 0) {
                    sidebarHeader

                    List(selection: $selectedJobID) {
                        if !activeJobs.isEmpty {
                            Section("下载中") {
                                ForEach(activeJobs) { job in
                                    sidebarRow(job)
                                        .tag(job.id)
                                }
                            }
                        }

                        if !completedJobs.isEmpty {
                            Section("已完成") {
                                ForEach(completedJobs) { job in
                                    sidebarRow(job)
                                        .tag(job.id)
                                        .contextMenu {
                                            Button("删除记录") {
                                                pendingDeleteJob = job
                                            }
                                        }
                                }
                            }
                        }

                        if !attentionJobs.isEmpty {
                            Section("需要处理") {
                                ForEach(attentionJobs) { job in
                                    sidebarRow(job)
                                        .tag(job.id)
                                        .contextMenu {
                                            Button("删除记录") {
                                                pendingDeleteJob = job
                                            }
                                        }
                                }
                            }
                        }
                    }
                    .searchable(text: $jobSearchText, prompt: "搜索素材")

                    sidebarFooter
                }
                .navigationTitle("素材库")
            } detail: {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if let job = visibleSelectedJob {
                            jobDetailPanel(job)
                        } else {
                            emptyHistoryPanel
                        }
                    }
                    .frame(maxWidth: 860, alignment: .leading)
                    .padding(20)
                    .padding(.bottom, 56)
                }
                .background(Color(NSColor.windowBackgroundColor))
                .navigationTitle(visibleSelectedJob?.mediaTitle ?? "素材库")
            }
            .frame(minWidth: 900, minHeight: 560)
            .toolbar {
                ToolbarItemGroup(placement: .primaryAction) {
                    Button {
                        Task { await controller.refreshJobs(store: store) }
                    } label: {
                        Label("刷新素材库", systemImage: "arrow.clockwise")
                            .labelStyle(.iconOnly)
                    }
                    .accessibilityLabel("刷新素材库")
                    .disabled(store.isLoading)

                    Button {
                        isCreatePanelPresented = true
                    } label: {
                        Label("新建任务", systemImage: "plus")
                            .labelStyle(.iconOnly)
                    }
                    .accessibilityLabel("新建任务")
                }
                ToolbarItem(placement: .status) {
                    HStack(spacing: 10) {
                        if store.backendHealthStatus == .unhealthy {
                            Label("后端未连接", systemImage: "xmark.circle.fill")
                                .foregroundStyle(.red)
                        } else if store.backendHealthStatus == .healthy {
                            Label("后端已连接", systemImage: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                        } else {
                            Label("检测中", systemImage: "clock")
                                .foregroundStyle(.secondary)
                        }
                        Button("重新检测") {
                            Task { await refreshBackendHealth(startIfNeeded: true) }
                        }
                        .buttonStyle(.borderless)
                    }
                    .font(.caption)
                }
            }
            .sheet(isPresented: $isCreatePanelPresented, onDismiss: resetPreviewState) {
                createTaskPanel()
            }
            .confirmationDialog(
                "清理全部历史？",
                isPresented: $isClearHistoryConfirmationPresented,
                titleVisibility: .visible
            ) {
                Button("清理全部历史", role: .destructive) {
                    clearAllHistory()
                }
                Button("取消", role: .cancel) {}
            } message: {
                Text("会删除所有已完成、失败或已取消的历史记录及对应本地文件；正在处理的任务会保留。")
            }
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
            .task {
                requestNotificationAuthorization()
                await startApp()
            }
            .task { await refreshBackendHealthPeriodically() }
            .onChange(of: store.jobs) { _, jobs in
                updateCompletionNotifications(for: jobs)
                if let selectedJobID, jobs.contains(where: { $0.id == selectedJobID }) {
                    return
                }
                selectedJobID = jobs.first?.id
            }
            .onChange(of: filteredJobs.map(\.id)) { _, visibleIDs in
                if let selectedJobID, visibleIDs.contains(selectedJobID) {
                    return
                }
                selectedJobID = visibleIDs.first
            }
            .task(id: visibleSelectedJob?.id) {
                await loadArtifacts(for: visibleSelectedJob)
                await loadJobLogs(for: visibleSelectedJob)
            }
            .task(id: visibleSelectedJob?.status) {
                await loadArtifacts(for: visibleSelectedJob)
                await loadJobLogs(for: visibleSelectedJob)
            }
            .task(id: store.registration?.accessToken) {
                await loadArtifacts(for: visibleSelectedJob)
                await loadJobLogs(for: visibleSelectedJob)
            }
        }
    }
}
