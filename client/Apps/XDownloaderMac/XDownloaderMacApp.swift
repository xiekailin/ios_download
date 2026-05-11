import AppCore
import AppKit
import IOKit.pwr_mgt
import IOKit.ps
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
        case .paused:
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

@MainActor
private final class MacDownloadActivityAssertion {
    private var assertionID = IOPMAssertionID(0)
    private var isActive = false

    func update(runningJobCount: Int) {
        if runningJobCount > 0 {
            acquire()
        } else {
            release()
        }
    }

    deinit {
        if isActive {
            IOPMAssertionRelease(assertionID)
        }
    }

    private func acquire() {
        guard !isActive else { return }
        var newAssertionID = IOPMAssertionID(0)
        let result = IOPMAssertionCreateWithName(
            kIOPMAssertionTypePreventUserIdleSystemSleep as CFString,
            IOPMAssertionLevel(kIOPMAssertionLevelOn),
            "XDownloader active transfer" as CFString,
            &newAssertionID
        )
        guard result == kIOReturnSuccess else { return }
        assertionID = newAssertionID
        isActive = true
    }

    private func release() {
        guard isActive else { return }
        IOPMAssertionRelease(assertionID)
        assertionID = IOPMAssertionID(0)
        isActive = false
    }
}

private struct TransferSpeedSparkline: View {
    let samples: [TransferSpeedSample]

    var body: some View {
        GeometryReader { geometry in
            let values = samples.map(\.bytesPerSecond)
            let peak = max(values.max() ?? 1, 1)
            let width = max(geometry.size.width, 1)
            let height = max(geometry.size.height, 1)

            ZStack(alignment: .bottomLeading) {
                Rectangle()
                    .fill(MacNativeStyle.subtleBackground)
                Path { path in
                    guard !values.isEmpty else {
                        path.move(to: CGPoint(x: 0, y: height))
                        path.addLine(to: CGPoint(x: width, y: height))
                        return
                    }
                    for (index, value) in values.enumerated() {
                        let x = values.count == 1 ? width : width * CGFloat(index) / CGFloat(values.count - 1)
                        let y = height - (CGFloat(value) / CGFloat(peak)) * height
                        if index == 0 {
                            path.move(to: CGPoint(x: x, y: y))
                        } else {
                            path.addLine(to: CGPoint(x: x, y: y))
                        }
                    }
                }
                .stroke(Color.accentColor, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
            }
        }
        .frame(height: 64)
        .clipShape(RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius)
                .stroke(MacNativeStyle.border, lineWidth: 1)
        )
    }
}

@main
struct XDownloaderMacApp: App {
    @Environment(\.openWindow) private var openWindow
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
    @State private var settingsSaveMessage: String?
    @State private var isRestartingBackend = false
    @State private var activeFailureRecoveryJobID: Job.ID?
    @State private var activeQueueActionJobID: Job.ID?
    @State private var isBatchRetryingJobs = false
    @State private var telemetryByJobID: [Job.ID: TransferTelemetry] = [:]
    @State private var downloadActivityAssertion = MacDownloadActivityAssertion()
    private static let localSettings = makeLocalSettings()
    private static let localSecret = localSettings.localBackendSecret
    private static let localBaseURL = URL(string: "http://127.0.0.1:18767")!
    private let settingsRepository = LocalAppSettingsRepository()
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

    private var queueOverview: JobQueueOverview {
        JobQueueOverview(jobs: filteredJobs)
    }

    private var systemQueueOverview: JobQueueOverview {
        JobQueueOverview(jobs: store.jobs)
    }

    private var runningJobs: [Job] {
        activeJobs.filter { JobQueueLane.lane(for: $0.status) == .running }
    }

    private var queuedJobs: [Job] {
        activeJobs.filter { JobQueueLane.lane(for: $0.status) == .queued }
    }

    private var pausedJobs: [Job] {
        activeJobs.filter { JobQueueLane.lane(for: $0.status) == .paused }
    }

    private var attentionJobs: [Job] {
        filteredJobs.filter { $0.status == .failed || $0.status == .canceled }
    }

    private var retryableJobs: [Job] {
        store.jobs.filter { $0.status == .failed || $0.status == .canceled }
    }

    private var menuBarTitle: String {
        if systemQueueOverview.runningCount > 0 {
            return "\(systemQueueOverview.runningCount) 下载中"
        }
        if systemQueueOverview.queuedCount > 0 {
            return "\(systemQueueOverview.queuedCount) 排队中"
        }
        if store.backendHealthStatus == .unhealthy {
            return "后端未连接"
        }
        return "XDownloader"
    }

    private var menuBarSystemImage: String {
        if systemQueueOverview.runningCount > 0 {
            return "arrow.down.circle.fill"
        }
        if systemQueueOverview.queuedCount > 0 {
            return "clock.fill"
        }
        if store.backendHealthStatus == .unhealthy {
            return "xmark.circle.fill"
        }
        return "tray.full"
    }

    private static func makeLocalSettings() -> AppSettings {
        let repository = LocalAppSettingsRepository()
        var settings = (try? repository.loadSettings()) ?? AppSettings()
        let supportDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "XDownloader", directoryHint: .isDirectory)
        let secretURL = supportDirectory.appending(path: "local_backend_secret")
        if let secret = try? String(contentsOf: secretURL, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines), !secret.isEmpty {
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: secretURL.path)
            settings.apiBaseURL = localBaseURL
            settings.localBackendSecret = secret
            try? repository.saveSettings(settings)
            return settings
        }
        let secret = settings.localBackendSecret.isEmpty ? LocalBackendLauncher.makeLocalSecret() : settings.localBackendSecret
        try? FileManager.default.createDirectory(at: supportDirectory, withIntermediateDirectories: true)
        try? secret.write(to: secretURL, atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: secretURL.path)
        settings.apiBaseURL = localBaseURL
        settings.localBackendSecret = secret
        try? repository.saveSettings(settings)
        return settings
    }

    private var downloadPerformanceBinding: Binding<DownloadPerformanceSettings> {
        Binding(
            get: { store.settings.downloadPerformance },
            set: { saveDownloadPerformanceSettings($0) }
        )
    }

    private func makeLocalBackendLauncher() -> LocalBackendLauncher {
        let performanceSettings = store.settings.downloadPerformance.resolvedForCurrentDevice(
            isExternalPowerConnected: isExternalPowerConnected()
        )
        return LocalBackendLauncher(
            environment: LocalBackendLauncher.defaultEnvironment(performanceSettings: performanceSettings),
            localSecret: store.settings.localBackendSecret
        )
    }

    private func saveDownloadPerformanceSettings(_ performance: DownloadPerformanceSettings) {
        var settings = store.settings
        settings.apiBaseURL = Self.localBaseURL
        settings.localBackendSecret = Self.localSecret
        settings.downloadPerformance = performance
        do {
            try settingsRepository.saveSettings(settings)
            store.setSettings(settings)
            settingsSaveMessage = "已保存，重启后端后生效。"
        } catch {
            store.setError("保存设置失败：\(error.localizedDescription)")
        }
    }

    private func resetDownloadPerformanceSettings() {
        saveDownloadPerformanceSettings(.balanced)
    }

    private func conservativeRecoveryPerformanceSettings() -> DownloadPerformanceSettings {
        var settings = DownloadPerformanceSettings.defaults(for: .lowPower)
        let current = store.settings.downloadPerformance
        settings.nightDownloadEnabled = current.nightDownloadEnabled
        settings.nightDownloadStartHour = current.nightDownloadStartHour
        settings.nightDownloadEndHour = current.nightDownloadEndHour
        return settings
    }

    private func automaticDownloadPerformanceSettings() -> DownloadPerformanceSettings {
        DownloadPerformanceSettings.automaticDefaultsForCurrentDevice(
            isExternalPowerConnected: isExternalPowerConnected()
        )
    }

    private func isExternalPowerConnected() -> Bool {
        guard let powerSourceInfo = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(),
              let powerSourceType = IOPSGetProvidingPowerSourceType(powerSourceInfo)?.takeUnretainedValue() as String? else {
            return true
        }
        return powerSourceType != (kIOPSBatteryPowerValue as String)
    }

    private func restartBackendForCurrentSettings() {
        Task {
            isRestartingBackend = true
            defer { isRestartingBackend = false }
            do {
                let status = try await makeLocalBackendLauncher().restartAndCheckHealth()
                store.setBackendHealthStatus(status)
                if status == .healthy {
                    settingsSaveMessage = "后端已重启，配置已生效。"
                    store.setError(nil)
                }
            } catch {
                store.setBackendHealthStatus(.unhealthy)
                store.setError(error.localizedDescription)
            }
        }
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
        do {
            let status = try await makeLocalBackendLauncher().startAndCheckHealth()
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
        let status = await makeLocalBackendLauncher().checkHealth()
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

    private func recordTransferTelemetry(for jobs: [Job]) {
        let currentJobIDs = Set(jobs.map(\.id))
        var nextTelemetry = telemetryByJobID.filter { currentJobIDs.contains($0.key) }
        for job in jobs {
            let telemetry = nextTelemetry[job.id] ?? TransferTelemetry()
            nextTelemetry[job.id] = telemetry.recording(job: job)
        }
        telemetryByJobID = nextTelemetry
    }

    private func updateSystemDownloadActivity(for jobs: [Job]) {
        let overview = JobQueueOverview(jobs: jobs)
        downloadActivityAssertion.update(runningJobCount: overview.runningCount)
    }

    private func showMainWindow() {
        openWindow(id: "main")
        NSApp.activate(ignoringOtherApps: true)
        NSApp.windows.first { $0.canBecomeMain }?.makeKeyAndOrderFront(nil)
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
                    if job.priority != 0, !job.status.isTerminal {
                        Text("·")
                        Text("优先 \(job.priority)")
                            .monospacedDigit()
                    }
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

    private func activeSidebarRow(_ job: Job) -> some View {
        sidebarRow(job)
            .tag(job.id)
            .contextMenu {
                activeJobContextMenu(for: job)
            }
    }

    @ViewBuilder
    private func activeJobContextMenu(for job: Job) -> some View {
        if job.status == .queued {
            Button("暂停") {
                pauseQueuedJob(job)
            }
        }
        if job.status == .paused {
            Button("继续") {
                resumeQueuedJob(job)
            }
        }
        Button("取消任务", role: .destructive) {
            cancelActiveJob(job)
        }
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

            queueOverviewPanel(queueOverview)
        }
        .padding(.horizontal, 12)
        .padding(.top, 12)
        .padding(.bottom, 8)
    }

    @ViewBuilder
    private func queueOverviewPanel(_ overview: JobQueueOverview) -> some View {
        if overview.totalActiveCount > 0 {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    queueOverviewMetric("处理", count: overview.runningCount, color: .accentColor)
                    queueOverviewMetric("排队", count: overview.queuedCount, color: .orange)
                    queueOverviewMetric("暂停", count: overview.pausedCount, color: .secondary)
                }

                if !overview.platformSummaries.isEmpty {
                    Divider()
                    VStack(alignment: .leading, spacing: 5) {
                        ForEach(overview.platformSummaries.prefix(3)) { item in
                            HStack(spacing: 6) {
                                Text(item.title)
                                    .lineLimit(1)
                                    .truncationMode(.tail)
                                Spacer(minLength: 8)
                                Text("\(item.count)")
                                    .monospacedDigit()
                                    .foregroundStyle(.secondary)
                            }
                            .font(.caption2)
                        }
                    }
                }
            }
            .padding(9)
            .background(MacNativeStyle.subtleBackground, in: RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius))
            .overlay(
                RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius)
                    .stroke(MacNativeStyle.border.opacity(0.7), lineWidth: 1)
            )
        }
    }

    private func queueOverviewMetric(_ title: String, count: Int, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text("\(count)")
                .font(.caption.monospacedDigit().weight(.semibold))
                .foregroundStyle(color)
            Text(title)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var sidebarFooter: some View {
        VStack(spacing: 8) {
            Divider()
            if !retryableJobs.isEmpty {
                Button {
                    batchRetryFailedJobs()
                } label: {
                    Label(isBatchRetryingJobs ? "正在重试…" : "批量重试失败任务", systemImage: "arrow.clockwise.circle")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderless)
                .controlSize(.regular)
                .disabled(store.isLoading || isBatchRetryingJobs)
                .padding(.horizontal, 12)
            }
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

    private func queueManagementSection(for job: Job) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    queueStatusActions(for: job)
                }
                VStack(alignment: .leading, spacing: 8) {
                    queueStatusActions(for: job)
                }
            }

            if job.status == .queued || job.status == .paused {
                HStack(spacing: 10) {
                    Label("优先级 \(job.priority)", systemImage: "flag")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                        .frame(minWidth: 92, alignment: .leading)

                    Button {
                        setPriority(for: job, value: job.priority + 10)
                    } label: {
                        Label("提高", systemImage: "arrow.up.circle")
                    }
                    .controlSize(.small)
                    .disabled(store.isLoading || activeQueueActionJobID != nil || job.priority >= 100)

                    Button {
                        setPriority(for: job, value: job.priority - 10)
                    } label: {
                        Label("降低", systemImage: "arrow.down.circle")
                    }
                    .controlSize(.small)
                    .disabled(store.isLoading || activeQueueActionJobID != nil || job.priority <= -100)

                    Button {
                        setPriority(for: job, value: 0)
                    } label: {
                        Label("重置", systemImage: "arrow.counterclockwise")
                    }
                    .controlSize(.small)
                    .disabled(store.isLoading || activeQueueActionJobID != nil || job.priority == 0)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func speedTelemetrySection(for job: Job) -> some View {
        let telemetry = telemetryByJobID[job.id] ?? TransferTelemetry()
        let bottleneck = telemetry.bottleneck(for: job)
        return VStack(alignment: .leading, spacing: 10) {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), alignment: .leading)], alignment: .leading, spacing: 8) {
                speedMetric("实时", value: job.speedText ?? "0 KB/s", systemImage: "speedometer")
                speedMetric("平均", value: telemetry.averageSpeedText, systemImage: "chart.line.uptrend.xyaxis")
                speedMetric("峰值", value: telemetry.peakSpeedText, systemImage: "bolt")
            }

            Label(bottleneck.title, systemImage: bottleneck.systemImage)
                .font(.caption.weight(.medium))
                .foregroundStyle(MacNativeStyle.statusColor(job.status))

            TransferSpeedSparkline(samples: telemetry.samples)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func speedMetric(_ title: String, value: String, systemImage: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                Text(value)
                    .font(.caption.monospacedDigit().weight(.medium))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
            }
        }
        .padding(8)
        .background(MacNativeStyle.subtleBackground, in: RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius))
    }

    @ViewBuilder
    private func queueStatusActions(for job: Job) -> some View {
        if activeQueueActionJobID == job.id {
            ProgressView()
                .controlSize(.small)
        }
        if job.status == .queued {
            Button {
                pauseQueuedJob(job)
            } label: {
                Label("暂停", systemImage: "pause.circle")
            }
            .buttonStyle(.bordered)
            .disabled(store.isLoading || activeQueueActionJobID != nil)
        }
        if job.status == .paused {
            Button {
                resumeQueuedJob(job)
            } label: {
                Label("继续", systemImage: "play.circle")
            }
            .buttonStyle(.borderedProminent)
            .disabled(store.isLoading || activeQueueActionJobID != nil)
        }
        if !job.status.isTerminal {
            Button(role: .destructive) {
                cancelActiveJob(job)
            } label: {
                Label("取消任务", systemImage: "xmark.circle")
            }
            .buttonStyle(.bordered)
            .disabled(store.isLoading || activeQueueActionJobID != nil)
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

    private func failureRecoveryAdviceCard(_ advice: FailureRecoveryAdvice, for job: Job) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "wrench.and.screwdriver.fill")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.red)
                .frame(width: 22, height: 22)
                .background(Color.red.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))

            VStack(alignment: .leading, spacing: 6) {
                Text(advice.title)
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(2)

                Text(advice.detail)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 8) {
                    Button {
                        performFailureRecovery(advice.action, for: job)
                    } label: {
                        if activeFailureRecoveryJobID == job.id {
                            Label("处理中…", systemImage: "clock")
                        } else {
                            Label(advice.actionTitle, systemImage: recoveryActionSystemImage(advice.action))
                        }
                    }
                    .controlSize(.small)
                    .buttonStyle(.borderedProminent)
                    .disabled(store.isLoading || retryingJobID != nil || activeFailureRecoveryJobID != nil)

                    if let secondaryAction = advice.secondaryAction,
                       let secondaryActionTitle = advice.secondaryActionTitle {
                        Button {
                            performFailureRecovery(secondaryAction, for: job)
                        } label: {
                            Label(secondaryActionTitle, systemImage: recoveryActionSystemImage(secondaryAction))
                        }
                        .controlSize(.small)
                        .buttonStyle(.bordered)
                        .disabled(store.isLoading || retryingJobID != nil || activeFailureRecoveryJobID != nil)
                    }
                }
                .padding(.top, 2)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.opacity(0.05), in: RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: MacNativeStyle.cornerRadius)
                .stroke(Color.red.opacity(0.18), lineWidth: 1)
        )
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

    private func retryFailedJob(_ job: Job, marksRecovery: Bool = false) {
        guard retryingJobID == nil, !store.isLoading else { return }
        retryingJobID = job.id
        if marksRecovery {
            activeFailureRecoveryJobID = job.id
        }
        resetArtifactState()
        Task {
            await controller.retryJob(id: job.id, store: store)
            retryingJobID = nil
            if marksRecovery {
                activeFailureRecoveryJobID = nil
            }
        }
    }

    private func batchRetryFailedJobs() {
        guard !isBatchRetryingJobs, !store.isLoading, !retryableJobs.isEmpty else { return }
        isBatchRetryingJobs = true
        resetArtifactState()
        Task {
            await controller.batchRetryJobs(store: store)
            isBatchRetryingJobs = false
        }
    }

    private func pauseQueuedJob(_ job: Job) {
        guard activeQueueActionJobID == nil, !store.isLoading else { return }
        activeQueueActionJobID = job.id
        Task {
            await controller.pauseJob(id: job.id, store: store)
            activeQueueActionJobID = nil
        }
    }

    private func resumeQueuedJob(_ job: Job) {
        guard activeQueueActionJobID == nil, !store.isLoading else { return }
        activeQueueActionJobID = job.id
        Task {
            await controller.resumeJob(id: job.id, store: store)
            activeQueueActionJobID = nil
        }
    }

    private func cancelActiveJob(_ job: Job) {
        guard activeQueueActionJobID == nil, !store.isLoading else { return }
        activeQueueActionJobID = job.id
        Task {
            await controller.cancelJob(id: job.id, store: store)
            activeQueueActionJobID = nil
        }
    }

    private func setPriority(for job: Job, value: Int) {
        guard activeQueueActionJobID == nil, !store.isLoading else { return }
        activeQueueActionJobID = job.id
        let boundedValue = min(100, max(-100, value))
        Task {
            await controller.setJobPriority(id: job.id, priority: boundedValue, store: store)
            activeQueueActionJobID = nil
        }
    }

    private func performFailureRecovery(_ action: FailureRecoveryAction, for job: Job) {
        switch action {
        case .retry:
            retryFailedJob(job, marksRecovery: true)
        case .uploadCookiesAndRetry:
            selectCookieFileAndRetry(job)
        case .recheckBackendAndRetry:
            recheckBackendAndRetry(job)
        case .applyConservativeModeAndRetry:
            applyConservativeModeAndRetry(job)
        case .openDownloadsFolder:
            openDownloadsFolderForRecovery(job)
        case .openSourceInBrowser:
            openSourceInBrowserForRecovery(job)
        case .inspectLogs:
            isLogSectionExpanded = true
            Task { await loadJobLogs(for: job) }
        }
    }

    private func selectCookieFileAndRetry(_ job: Job) {
        guard activeFailureRecoveryJobID == nil, !store.isLoading else { return }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType.plainText, UTType(filenameExtension: "txt")].compactMap { $0 }
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.message = "选择平台导出的 cookies.txt"
        if panel.runModal() == .OK, let url = panel.url {
            activeFailureRecoveryJobID = job.id
            resetArtifactState()
            Task {
                let didStartAccessing = url.startAccessingSecurityScopedResource()
                defer {
                    if didStartAccessing {
                        url.stopAccessingSecurityScopedResource()
                    }
                    activeFailureRecoveryJobID = nil
                }
                if let status = await controller.uploadYouTubeCookies(fileURL: url, store: store), status.isConfigured {
                    await controller.retryJob(id: job.id, store: store)
                }
            }
        }
    }

    private func recheckBackendAndRetry(_ job: Job) {
        guard activeFailureRecoveryJobID == nil, !store.isLoading else { return }
        activeFailureRecoveryJobID = job.id
        resetArtifactState()
        Task {
            await refreshBackendHealth(startIfNeeded: true)
            if store.backendHealthStatus == .healthy {
                await controller.retryJob(id: job.id, store: store)
            }
            activeFailureRecoveryJobID = nil
        }
    }

    private func applyConservativeModeAndRetry(_ job: Job) {
        guard activeFailureRecoveryJobID == nil, !store.isLoading else { return }
        activeFailureRecoveryJobID = job.id
        resetArtifactState()
        saveDownloadPerformanceSettings(conservativeRecoveryPerformanceSettings())
        Task {
            isRestartingBackend = true
            defer {
                isRestartingBackend = false
                activeFailureRecoveryJobID = nil
            }
            do {
                let status = try await makeLocalBackendLauncher().restartAndCheckHealth()
                store.setBackendHealthStatus(status)
                if status == .healthy {
                    settingsSaveMessage = "已切到稳妥下载模式并重启后端。"
                    store.setError(nil)
                    await controller.retryJob(id: job.id, store: store)
                } else {
                    store.setError("后端未连接，暂时无法自动重试。")
                }
            } catch {
                store.setBackendHealthStatus(.unhealthy)
                store.setError(error.localizedDescription)
            }
        }
    }

    private func openDownloadsFolderForRecovery(_ job: Job) {
        guard activeFailureRecoveryJobID == nil else { return }
        activeFailureRecoveryJobID = job.id
        defer { activeFailureRecoveryJobID = nil }
        do {
            let directory = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
                .appending(path: "XDownloader", directoryHint: .isDirectory)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            NSWorkspace.shared.activateFileViewerSelecting([directory])
            store.setError(nil)
        } catch {
            store.setError("打开下载目录失败：\(error.localizedDescription)")
        }
    }

    private func openSourceInBrowserForRecovery(_ job: Job) {
        guard let url = URL(string: job.sourceURL), NSWorkspace.shared.open(url) else {
            store.setError("打开源链接失败，请复制链接后在浏览器中检查。")
            return
        }
        store.setError(nil)
    }

    private func recoveryActionSystemImage(_ action: FailureRecoveryAction) -> String {
        switch action {
        case .retry:
            "arrow.clockwise"
        case .uploadCookiesAndRetry:
            "key.fill"
        case .recheckBackendAndRetry:
            "network"
        case .applyConservativeModeAndRetry:
            "speedometer"
        case .openDownloadsFolder:
            "folder"
        case .openSourceInBrowser:
            "safari"
        case .inspectLogs:
            "list.bullet.rectangle"
        }
    }

    private func terminalJobActions(for job: Job) -> some View {
        HStack(spacing: 8) {
            if job.status == .failed || job.status == .canceled {
                Button {
                    retryFailedJob(job)
                } label: {
                    Label(retryingJobID == job.id ? "正在重试…" : "重试任务", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.borderedProminent)
                .disabled(store.isLoading || retryingJobID != nil || activeFailureRecoveryJobID != nil)
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

            Divider()
            inspectorSection("速度", systemImage: "waveform.path.ecg") {
                speedTelemetrySection(for: job)
            }

            if !job.status.isTerminal {
                Divider()
                inspectorSection("队列", systemImage: "list.number") {
                    queueManagementSection(for: job)
                }
            }

            if let advice = job.failureRecoveryAdvice {
                Divider()
                inspectorSection("诊断建议", systemImage: "stethoscope") {
                    failureRecoveryAdviceCard(advice, for: job)
                }
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

    private var materialSidebar: some View {
        VStack(spacing: 0) {
            sidebarHeader
            materialList
                .searchable(text: $jobSearchText, prompt: "搜索素材")
            sidebarFooter
        }
        .navigationTitle("素材库")
    }

    private var materialList: some View {
        List(selection: $selectedJobID) {
            activeJobsSection(JobQueueLane.running.title, jobs: runningJobs)
            activeJobsSection(JobQueueLane.queued.title, jobs: queuedJobs)
            activeJobsSection(JobQueueLane.paused.title, jobs: pausedJobs)
            terminalJobsSection("已完成", jobs: completedJobs)
            terminalJobsSection("需要处理", jobs: attentionJobs)
        }
    }

    @ViewBuilder
    private func activeJobsSection(_ title: String, jobs: [Job]) -> some View {
        if !jobs.isEmpty {
            Section(title) {
                ForEach(jobs) { job in
                    activeSidebarRow(job)
                }
            }
        }
    }

    @ViewBuilder
    private func terminalJobsSection(_ title: String, jobs: [Job]) -> some View {
        if !jobs.isEmpty {
            Section(title) {
                ForEach(jobs) { job in
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

    private var materialDetail: some View {
        ScrollView {
            selectedJobDetailContent
                .frame(maxWidth: 860, alignment: .leading)
                .padding(20)
                .padding(.bottom, 56)
        }
        .background(Color(NSColor.windowBackgroundColor))
        .navigationTitle(visibleSelectedJob?.mediaTitle ?? "素材库")
    }

    @ViewBuilder
    private var selectedJobDetailContent: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let job = visibleSelectedJob {
                jobDetailPanel(job)
            } else {
                emptyHistoryPanel
            }
        }
    }

    var body: some Scene {
        WindowGroup("XDownloader", id: "main") {
            NavigationSplitView {
                materialSidebar
            } detail: {
                materialDetail
            }
            .frame(minWidth: 900, minHeight: 560)
            .toolbar {
                ToolbarItemGroup(placement: .primaryAction) {
                    SettingsLink {
                        Label("设置", systemImage: "gearshape")
                            .labelStyle(.iconOnly)
                    }
                    .accessibilityLabel("设置")

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
                updateSystemDownloadActivity(for: store.jobs)
            }
            .task { await refreshBackendHealthPeriodically() }
            .onChange(of: store.jobs) { _, jobs in
                updateCompletionNotifications(for: jobs)
                recordTransferTelemetry(for: jobs)
                updateSystemDownloadActivity(for: jobs)
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

        MenuBarExtra {
            Text(menuBarTitle)
            if systemQueueOverview.runningCount > 0 {
                Text("正在处理 \(systemQueueOverview.runningCount) 个任务")
            }
            if systemQueueOverview.queuedCount > 0 {
                Text("排队 \(systemQueueOverview.queuedCount) 个任务")
            }
            if systemQueueOverview.pausedCount > 0 {
                Text("暂停 \(systemQueueOverview.pausedCount) 个任务")
            }
            Divider()
            Button("打开主窗口") {
                showMainWindow()
            }
            Button("刷新素材库") {
                Task { await controller.refreshJobs(store: store) }
            }
            .disabled(store.isLoading)
            Button("重新检测后端") {
                Task { await refreshBackendHealth(startIfNeeded: true) }
            }
            Divider()
            Button("退出 XDownloader") {
                NSApp.terminate(nil)
            }
        } label: {
            Label(menuBarTitle, systemImage: menuBarSystemImage)
        }
        .menuBarExtraStyle(.menu)

        Settings {
            MacDownloadPerformanceSettingsView(
                performance: downloadPerformanceBinding,
                statusMessage: settingsSaveMessage,
                isRestartingBackend: isRestartingBackend,
                automaticDefaults: automaticDownloadPerformanceSettings,
                resetToBalanced: resetDownloadPerformanceSettings,
                restartBackend: restartBackendForCurrentSettings
            )
        }
    }
}

private struct MacDownloadPerformanceSettingsView: View {
    @Binding var performance: DownloadPerformanceSettings
    let statusMessage: String?
    let isRestartingBackend: Bool
    let automaticDefaults: () -> DownloadPerformanceSettings
    let resetToBalanced: () -> Void
    let restartBackend: () -> Void

    private let jobOptions = [1, 2, 3, 4]
    private let connectionOptions = [1, 4, 8, 16]
    private let fragmentOptions = [1, 4, 8, 16]
    private let segmentSizeOptions = [4 * 1024 * 1024, 8 * 1024 * 1024, 16 * 1024 * 1024]
    private let ffmpegThreadOptions = [0, 1, 2, 4, 8]
    private let hourOptions = Array(0...23)
    private var usesAutomaticPerformance: Bool { performance.performanceMode == .automatic }

    var body: some View {
        Form {
            Section("下载与性能") {
                Picker("性能模式", selection: $performance.performanceMode) {
                    ForEach(DownloadPerformanceMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .onChange(of: performance.performanceMode) { _, mode in
                    performance = mode == .automatic ? automaticDefaults() : DownloadPerformanceSettings.defaults(for: mode)
                }

                if usesAutomaticPerformance {
                    Text("自动模式默认使用高速；未插电会降到均衡，低电量或系统发热会降到省电。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Picker("同时下载任务", selection: $performance.simultaneousDownloadJobs) {
                    ForEach(jobOptions, id: \.self) { value in
                        Text("\(value)").tag(value)
                    }
                }
                .disabled(usesAutomaticPerformance)

                Toggle("直连分片下载", isOn: $performance.directDownloadAccelerationEnabled)
                    .disabled(usesAutomaticPerformance)

                Picker("直连连接数", selection: $performance.directDownloadMaxConnections) {
                    ForEach(connectionOptions, id: \.self) { value in
                        Text("\(value)").tag(value)
                    }
                }
                .disabled(usesAutomaticPerformance || !performance.directDownloadAccelerationEnabled)

                Picker("分片大小", selection: $performance.directDownloadSegmentSizeBytes) {
                    ForEach(segmentSizeOptions, id: \.self) { value in
                        Text(byteSizeTitle(value)).tag(value)
                    }
                }
                .disabled(usesAutomaticPerformance || !performance.directDownloadAccelerationEnabled)

                Picker("yt-dlp 分片", selection: $performance.ytdlpConcurrentFragments) {
                    ForEach(fragmentOptions, id: \.self) { value in
                        Text("\(value)").tag(value)
                    }
                }
                .disabled(usesAutomaticPerformance)

                Picker("合并与转码线程", selection: $performance.ffmpegThreadCount) {
                    ForEach(ffmpegThreadOptions, id: \.self) { value in
                        Text(value == 0 ? "自动" : "\(value)").tag(value)
                    }
                }
                .disabled(usesAutomaticPerformance)

                TextField("速度限制", text: $performance.downloadRateLimit, prompt: Text("不限"))
                    .textFieldStyle(.roundedBorder)
            }

            Section("队列") {
                Toggle("夜间下载", isOn: $performance.nightDownloadEnabled)

                Picker("开始时间", selection: $performance.nightDownloadStartHour) {
                    ForEach(hourOptions, id: \.self) { value in
                        Text(hourTitle(value)).tag(value)
                    }
                }
                .disabled(!performance.nightDownloadEnabled)

                Picker("结束时间", selection: $performance.nightDownloadEndHour) {
                    ForEach(hourOptions, id: \.self) { value in
                        Text(hourTitle(value)).tag(value)
                    }
                }
                .disabled(!performance.nightDownloadEnabled)
            }

            Section {
                HStack {
                    Button("恢复均衡默认值", action: resetToBalanced)
                    Spacer()
                    Button {
                        restartBackend()
                    } label: {
                        if isRestartingBackend {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Text("应用并重启后端")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(isRestartingBackend)
                }
                if let statusMessage {
                    Text(statusMessage)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .formStyle(.grouped)
        .padding(22)
        .frame(width: 520)
    }

    private func byteSizeTitle(_ value: Int) -> String {
        "\(value / 1024 / 1024) MB"
    }

    private func hourTitle(_ value: Int) -> String {
        String(format: "%02d:00", value)
    }
}
