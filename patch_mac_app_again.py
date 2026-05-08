import sys

with open("client/Apps/XDownloaderMac/XDownloaderMacApp.swift", "r") as f:
    content = f.read()

search_start = """    @ViewBuilder
    private var submissionPanel: some View {"""

idx = content.find(search_start)
if idx == -1:
    print("Could not find submissionPanel start")
    sys.exit(1)

new_content = content[:idx] + """    @ViewBuilder
    private var submissionPanel: some View {
        VStack(alignment: .leading, spacing: 24) {
            Picker("任务类型", selection: $submissionMode) {
                ForEach(SubmissionMode.allCases) { mode in
                    Text(mode.title).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .controlSize(.large)

            VStack(alignment: .leading, spacing: 16) {
                switch submissionMode {
                case .link:
                    VStack(alignment: .leading, spacing: 8) {
                        Text("分享链接")
                            .font(.headline)
                            .foregroundStyle(.secondary)
                        TextField("粘贴包含分享链接的文本", text: $store.draftURL)
                            .textFieldStyle(.roundedBorder)
                            .controlSize(.large)
                            .font(.body)
                        HStack {
                            Button(action: {
                                Task {
                                    successMessage = nil
                                    selectSubmittedJob(await controller.submitCurrentURL(store: store))
                                }
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
                            
                            Button("刷新") {
                                Task {
                                    successMessage = nil
                                    await controller.refreshJobs(store: store)
                                }
                            }
                            .controlSize(.large)
                            .disabled(store.isLoading)
                        }
                        
                        GroupBox("批量下载") {
                            VStack(alignment: .leading, spacing: 8) {
                                Text("每行一个链接，也可以粘贴包含多条分享链接的文本。批量下载当前仅支持普通下载。")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                TextEditor(text: $store.batchDraftText)
                                    .font(.body)
                                    .frame(minHeight: 92)
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 8)
                                            .stroke(.quaternary)
                                    )
                                    .disabled(store.isLoading)
                                    .accessibilityLabel("批量下载链接")
                                    .accessibilityHint("每行一个链接，也可以粘贴包含多条分享链接的文本。")
                                Button(store.isLoading ? "正在批量创建…" : "批量创建") {
                                    Task { await submitBatchURLs() }
                                }
                                .buttonStyle(.borderedProminent)
                                .disabled(store.isLoading || store.batchDraftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                            }
                        }
                        .padding(.top, 8)
                    }
                case .audioDownload:
                    VStack(alignment: .leading, spacing: 8) {
                        Text("视频链接")
                            .font(.headline)
                            .foregroundStyle(.secondary)
                        TextField("粘贴包含视频链接的文本", text: $store.draftURL)
                            .textFieldStyle(.roundedBorder)
                            .controlSize(.large)
                            .font(.body)
                        Text("只下载视频音频，并自动转换成 MP3。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        HStack {
                            Button(action: {
                                Task {
                                    successMessage = nil
                                    selectSubmittedJob(await controller.submitAudioDownloadURL(store: store))
                                }
                            }) {
                                HStack {
                                    Image(systemName: "music.note")
                                    Text(store.isLoading ? "正在创建…" : "下载 MP3")
                                }
                                .frame(minWidth: 100)
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.large)
                            .disabled(store.isLoading)
                            
                            Button("刷新") {
                                Task {
                                    successMessage = nil
                                    await controller.refreshJobs(store: store)
                                }
                            }
                            .controlSize(.large)
                            .disabled(store.isLoading)
                        }
                    }
                case .audioSeparation:
                    VStack(alignment: .leading, spacing: 12) {
                        Text("分离人声和伴奏")
                            .font(.headline)
                            .foregroundStyle(.secondary)
                        Text("支持 mp3、wav、m4a、aac、flac，最大 200MB。")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        
                        HStack(spacing: 12) {
                            Button(action: {
                                successMessage = nil
                                isAudioFileImporterPresented = true
                            }) {
                                HStack {
                                    Image(systemName: "doc.badge.plus")
                                    Text(selectedAudioFileURL?.lastPathComponent ?? "选择音乐文件")
                                        .lineLimit(1)
                                        .truncationMode(.middle)
                                }
                                .frame(maxWidth: 240)
                            }
                            .controlSize(.large)
                            
                            Button(action: {
                                Task { await submitSelectedAudioFile() }
                            }) {
                                HStack {
                                    Image(systemName: "waveform.badge.magnifyingglass")
                                    Text(store.isLoading ? "正在上传…" : "开始分离")
                                }
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.large)
                            .disabled(selectedAudioFileURL == nil || store.isLoading)
                            
                            Button("刷新") {
                                Task {
                                    successMessage = nil
                                    await controller.refreshJobs(store: store)
                                }
                            }
                            .controlSize(.large)
                            .disabled(store.isLoading)
                        }
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
        }
    }

    @ViewBuilder
    private var settingsPanel: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("设置")
                .font(.title2.bold())
                .padding(.top, 16)
            
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("YouTube 云端 Cookie")
                        .font(.headline)
                    Text("先配置 HTTPS 云端 API 地址和邀请码，再选择 Netscape 格式 cookies.txt 上传到云端。")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                
                VStack(spacing: 12) {
                    TextField("云端 API 地址 (如 https://example.com:18767)", text: $cloudBaseURLDraft)
                        .textFieldStyle(.roundedBorder)
                        .controlSize(.large)
                    
                    SecureField("服务器邀请码", text: $cloudBootstrapCodeDraft)
                        .textFieldStyle(.roundedBorder)
                        .controlSize(.large)
                }
                
                HStack(alignment: .center) {
                    Button(action: { saveCloudSettings() }) {
                        Text("保存云端配置")
                            .frame(minWidth: 100)
                    }
                    .controlSize(.large)
                    .buttonStyle(.bordered)
                    
                    if hasCloudSettings {
                        Text("当前：\(cloudSettings.apiBaseURL.absoluteString)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                }
                
                if let message = cloudCookieUnavailableMessage {
                    Label(message, systemImage: "lock.trianglebadge.exclamationmark")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.orange)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.orange.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
                }
                
                Divider().padding(.vertical, 8)
                
                VStack(alignment: .leading, spacing: 12) {
                    if let status = store.youtubeCookieStatus {
                        HStack {
                            Image(systemName: status.isConfigured ? "checkmark.seal.fill" : "xmark.seal.fill")
                                .foregroundStyle(status.isConfigured ? .green : .secondary)
                            Text(status.isConfigured ? "云端已配置 Cookie" : "云端未配置 Cookie")
                                .font(.subheadline.weight(.semibold))
                            if let fileSize = status.fileSize {
                                Text("(\(ByteCountFormatter.string(fromByteCount: Int64(fileSize), countStyle: .file)))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    
                    HStack(spacing: 12) {
                        Button(action: {
                            successMessage = nil
                            isCookieFileImporterPresented = true
                        }) {
                            HStack {
                                Image(systemName: "doc.text")
                                Text(selectedCookieFileURL?.lastPathComponent ?? "选择 cookies.txt")
                                    .lineLimit(1)
                            }
                            .frame(maxWidth: 180)
                        }
                        .controlSize(.large)
                        .disabled(!isCloudCookieManagementAvailable || isYouTubeCloudCookieRequestInFlight)
                        
                        Button(action: {
                            Task { await uploadSelectedCookieFile() }
                        }) {
                            Text(isYouTubeCloudCookieRequestInFlight ? "处理中…" : "上传到云端")
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                        .disabled(!isCloudCookieManagementAvailable || selectedCookieFileURL == nil || isYouTubeCloudCookieRequestInFlight)
                        
                        Button("检查状态") {
                            Task { await refreshCloudCookieStatus() }
                        }
                        .controlSize(.large)
                        .disabled(!isCloudCookieManagementAvailable || isYouTubeCloudCookieRequestInFlight)
                        
                        Spacer()
                        
                        Button(role: .destructive, action: {
                            isConfirmingDeleteCloudCookie = true
                        }) {
                            Image(systemName: "trash")
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                        .disabled(!isCloudCookieManagementAvailable || isYouTubeCloudCookieRequestInFlight || store.youtubeCookieStatus?.isConfigured != true)
                        .help("删除云端 Cookie")
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
        }
    }

    @ViewBuilder
    private func jobDetail(_ job: Job) -> some View {
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
            
            if job.status == .completed {
                if job.jobType == .audioSeparation {
                    audioArtifactActions(for: job)
                        .task(id: "\(job.id)-\(job.updatedAt.timeIntervalSince1970)") {
                            await refreshArtifacts(for: job)
                        }
                } else if job.artifactID != nil {
                    mediaArtifactActions(for: job)
                        .task(id: "\(job.id)-\(job.updatedAt.timeIntervalSince1970)") {
                            await refreshArtifacts(for: job)
                        }
                }
            }
            
            if !job.status.isTerminal {
                HStack(spacing: 12) {
                    ProgressView().controlSize(.small)
                    Text("任务正在处理中，完成后会在这里显示操作按钮。")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("取消任务", role: .destructive) {
                        Task { await controller.cancelJob(id: job.id, store: store) }
                    }
                    .controlSize(.large)
                    .disabled(store.isLoading)
                }
                .padding()
                .background(Color.accentColor.opacity(0.05))
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            
            if job.status == .completed && job.artifactID == nil && job.jobType != .audioSeparation {
                Text("暂无可操作文件。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding()
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.secondary.opacity(0.05))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            
            if job.status == .failed || job.status == .canceled {
                HStack {
                    Button(action: {
                        Task { await retryJob(job) }
                    }) {
                        HStack {
                            Image(systemName: "arrow.clockwise")
                            Text("重试任务")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(store.isLoading)
                }
                .padding(.top, 8)
            }
            
            if job.status.isTerminal {
                HStack {
                    Button(role: .destructive, action: {
                        pendingDeleteJob = job
                    }) {
                        HStack {
                            Image(systemName: "trash")
                            Text("删除文件和记录")
                        }
                    }
                    .controlSize(.large)
                    .disabled(store.isLoading || activeArtifactActionID != nil)
                }
                .padding(.top, 8)
            }
        }
    }

    @ViewBuilder
    private func mediaArtifactActions(for job: Job) -> some View {
        if let artifactID = job.artifactID {
            VStack(alignment: .leading, spacing: 16) {
                Text("文件操作")
                    .font(.headline)
                
                mediaArtifactDetails(for: job, artifactID: artifactID)
                
                HStack(spacing: 12) {
                    Button(action: {
                        Task { await revealArtifact(for: job) }
                    }) {
                        HStack {
                            Image(systemName: "magnifyingglass")
                            Text("在 Finder 中显示")
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                    
                    Button(action: {
                        Task { await copyArtifact(for: job) }
                    }) {
                        HStack {
                            Image(systemName: "doc.on.doc")
                            Text("复制文件")
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    
                    if let artifact = cachedArtifactSummary(id: artifactID) {
                        Button(role: .destructive, action: {
                            pendingDeleteArtifact = artifact
                        }) {
                            Image(systemName: "trash")
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                        .help("删除源文件")
                    }
                }
                .disabled(store.isLoading || activeArtifactActionID != nil)
                
                artifactActionStatus(artifactID: artifactID)
            }
            .padding(20)
            .background(Color(NSColor.controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .shadow(color: Color.black.opacity(0.05), radius: 4, x: 0, y: 2)
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.secondary.opacity(0.1), lineWidth: 1)
            )
        }
    }

    @ViewBuilder
    private func mediaArtifactDetails(for job: Job, artifactID: String) -> some View {
        switch artifactStates[job.id] {
        case .loading:
            HStack {
                ProgressView().controlSize(.small)
                Text("正在读取媒体详情…")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        case let .failed(message):
            HStack {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.red)
                Button("重新读取详情") {
                    Task { await refreshArtifacts(for: job, force: true) }
                }
                .buttonStyle(.bordered)
            }
        case let .loaded(artifacts):
            if let artifact = artifacts.first(where: { $0.id == artifactID }) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(artifact.fileName)
                        .font(.body.weight(.medium))
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .help(artifact.fileName)
                    if let mediaDetailsText = artifact.mediaDetailsText {
                        Text(mediaDetailsText)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            } else {
                HStack {
                    Text("未找到媒体详情。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("重新读取详情") {
                        Task { await refreshArtifacts(for: job, force: true) }
                    }
                    .buttonStyle(.bordered)
                }
            }
        case nil:
            EmptyView()
        }
    }

    @ViewBuilder
    private func audioArtifactActions(for job: Job) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("分离结果")
                .font(.headline)
            
            switch artifactStates[job.id] ?? .loading {
            case .loading:
                HStack(spacing: 12) {
                    ProgressView().controlSize(.small)
                    Text("正在读取人声和伴奏文件…")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding()
            case let .failed(message):
                VStack(alignment: .leading, spacing: 12) {
                    Text(message)
                        .font(.subheadline)
                        .foregroundStyle(.red)
                    Button("重新读取结果") {
                        Task { await refreshArtifacts(for: job, force: true) }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                }
                .padding()
            case let .loaded(artifacts):
                if artifacts.isEmpty {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("暂无分离结果，请确认音频分离工具已生成 vocals 和 accompaniment 文件。")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        Button("重新读取结果") {
                            Task { await refreshArtifacts(for: job, force: true) }
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                    }
                    .padding()
                } else {
                    VStack(spacing: 16) {
                        ForEach(artifacts) { artifact in
                            VStack(alignment: .leading, spacing: 12) {
                                HStack {
                                    Text(artifactTitle(artifact.role))
                                        .font(.subheadline.bold())
                                    Spacer()
                                }
                                
                                Text(artifact.fileName)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                
                                if let mediaDetailsText = artifact.mediaDetailsText {
                                    Text(mediaDetailsText)
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                                
                                HStack(spacing: 12) {
                                    Button(action: {
                                        Task { await revealArtifact(id: artifact.id) }
                                    }) {
                                        HStack {
                                            Image(systemName: "magnifyingglass")
                                            Text("在 Finder 中显示")
                                        }
                                        .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(.bordered)
                                    .controlSize(.large)
                                    
                                    Button(action: {
                                        Task { await copyArtifact(id: artifact.id) }
                                    }) {
                                        HStack {
                                            Image(systemName: "doc.on.doc")
                                            Text("复制文件")
                                        }
                                        .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(.borderedProminent)
                                    .controlSize(.large)
                                    
                                    Button(role: .destructive, action: {
                                        pendingDeleteArtifact = artifact
                                    }) {
                                        Image(systemName: "trash")
                                    }
                                    .buttonStyle(.bordered)
                                    .controlSize(.large)
                                }
                                .disabled(store.isLoading || activeArtifactActionID != nil)
                                
                                artifactActionStatus(artifactID: artifact.id)
                            }
                            .padding(16)
                            .background(Color.secondary.opacity(0.03))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    }
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
    }

    @ViewBuilder
    private func artifactActionStatus(artifactID: String) -> some View {
        HStack(spacing: 6) {
            if activeArtifactActionID == artifactID {
                ProgressView()
                    .controlSize(.small)
            }
            Text(artifactActionStatusText(for: artifactID))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }

    var body: some Scene {
        WindowGroup {
            NavigationSplitView {
                List(selection: $detailRoute) {
                    Section {
                        Label("新建任务", systemImage: "plus.app.fill")
                            .font(.headline)
                            .padding(.vertical, 4)
                            .tag(DetailRoute.newTask)
                    }
                    
                    Section("历史") {
                        if store.jobs.contains(where: { $0.status.isTerminal }) {
                            Button(role: .destructive, action: {
                                isConfirmingDeleteAllHistory = true
                            }) {
                                Label("清空历史记录", systemImage: "trash")
                            }
                            .disabled(store.isLoading || activeArtifactActionID != nil)
                        }
                        
                        if store.jobs.isEmpty {
                            Text("暂无历史任务")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .padding(.vertical, 8)
                        }
                        
                        ForEach(store.jobs) { job in
                            VStack(alignment: .leading, spacing: 6) {
                                Text(job.mediaTitle ?? job.sourceURL)
                                    .font(.subheadline.weight(.medium))
                                    .lineLimit(2)
                                HStack {
                                    TaskStatusBadge(status: job.status)
                                    Spacer()
                                    if let author = job.authorHandle {
                                        Text(author)
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(1)
                                    }
                                }
                            }
                            .padding(.vertical, 4)
                            .tag(DetailRoute.job(job.id))
                            .contextMenu {
                                if job.status == .failed || job.status == .canceled {
                                    Button("重试") {
                                        Task { await retryJob(job) }
                                    }
                                    .disabled(store.isLoading)
                                }
                                if job.status.isTerminal {
                                    Button("删除文件和记录") {
                                        pendingDeleteJob = job
                                    }
                                    .disabled(store.isLoading || activeArtifactActionID != nil)
                                }
                            }
                        }
                    }
                }
                .navigationTitle("XDownloader")
                .frame(minWidth: 260)
            } detail: {
                ScrollView {
                    VStack(alignment: .leading, spacing: 24) {
                        switch detailRoute ?? .newTask {
                        case .newTask:
                            VStack(alignment: .leading, spacing: 8) {
                                Text("创建新任务")
                                    .font(.system(size: 32, weight: .bold))
                                Text("选择下载、转 MP3 或音频分离，创建后会自动跳到对应历史任务。")
                                    .font(.body)
                                    .foregroundStyle(.secondary)
                            }
                            .padding(.bottom, 8)
                            
                            submissionPanel
                            settingsPanel
                        case .job:
                            if let job = selectedJob {
                                jobDetail(job)
                            } else {
                                VStack(alignment: .center, spacing: 16) {
                                    Image(systemName: "doc.text.magnifyingglass")
                                        .font(.system(size: 48))
                                        .foregroundStyle(.tertiary)
                                    Text("任务不存在")
                                        .font(.title.bold())
                                    Text("这条历史记录可能已被删除。")
                                        .foregroundStyle(.secondary)
                                }
                                .frame(maxWidth: .infinity, minHeight: 400)
                            }
                        }
                    }
                    .frame(maxWidth: 800, alignment: .leading)
                    .padding(36)
                    .padding(.bottom, 56)
                }
                .background(Color(NSColor.windowBackgroundColor))
            }
            .frame(minWidth: 900, minHeight: 600)
            .confirmationDialog(
                "删除这条下载记录和文件？",
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
                Button("删除文件和记录", role: .destructive) {
                    guard !store.isLoading, activeArtifactActionID == nil, let pendingDeleteJob else { return }
                    let deletedJobID = pendingDeleteJob.id
                    Task {
                        successMessage = nil
                        await controller.deleteJob(id: deletedJobID, store: store)
                        if !store.jobs.contains(where: { $0.id == deletedJobID }) {
                            artifactStates.removeValue(forKey: deletedJobID)
                            localArtifactURLs.removeAll()
                            if detailRoute == .job(deletedJobID) {
                                detailRoute = .newTask
                            }
                            successMessage = "已删除文件和这条历史记录。"
                        }
                    }
                    self.pendingDeleteJob = nil
                }
                Button("取消", role: .cancel) {
                    pendingDeleteJob = nil
                }
            } message: {
                if let fileName = pendingDeleteJob?.mediaTitle {
                    Text("会删除下载文件和这条历史记录，处理中任务不能删除。\\n\\n任务：\\(fileName)")
                } else {
                    Text("会删除下载文件和这条历史记录，处理中任务不能删除。")
                }
            }
            .confirmationDialog(
                "删除这个源文件？",
                isPresented: Binding(
                    get: { pendingDeleteArtifact != nil },
                    set: { isPresented in
                        if !isPresented {
                            pendingDeleteArtifact = nil
                        }
                    }
                ),
                titleVisibility: .visible
            ) {
                Button("删除源文件", role: .destructive) {
                    guard !store.isLoading, activeArtifactActionID == nil, let artifact = pendingDeleteArtifact else { return }
                    Task { await deleteArtifact(artifact) }
                    pendingDeleteArtifact = nil
                }
                Button("取消", role: .cancel) {
                    pendingDeleteArtifact = nil
                }
            } message: {
                if let fileName = pendingDeleteArtifact?.fileName {
                    Text("只删除下载文件，历史记录会保留。\\n\\n文件：\\(fileName)")
                } else {
                    Text("只删除下载文件，历史记录会保留。")
                }
            }
            .confirmationDialog(
                "删除所有历史文件和记录？",
                isPresented: $isConfirmingDeleteAllHistory,
                titleVisibility: .visible
            ) {
                Button("删除全部历史", role: .destructive) {
                    guard !store.isLoading, activeArtifactActionID == nil else { return }
                    Task { await deleteHistory() }
                }
                Button("取消", role: .cancel) {}
            } message: {
                let terminalCount = store.jobs.filter(\.status.isTerminal).count
                Text("会不可恢复地删除 \\(terminalCount) 条已完成、失败或已取消任务的下载文件和历史记录，处理中任务会保留。")
            }
            .confirmationDialog(
                "删除云端 YouTube Cookie？",
                isPresented: $isConfirmingDeleteCloudCookie,
                titleVisibility: .visible
            ) {
                Button("删除云端 Cookie", role: .destructive) {
                    Task { await deleteCloudCookieFile() }
                }
                Button("取消", role: .cancel) {}
            } message: {
                Text("会删除云端 YouTube 登录 Cookie，手机端下载需要登录验证的 YouTube 链接可能会失败。")
            }
            .fileImporter(
                isPresented: $isAudioFileImporterPresented,
                allowedContentTypes: supportedAudioContentTypes,
                allowsMultipleSelection: false
            ) { result in
                switch result {
                case let .success(urls):
                    successMessage = nil
                    guard let fileURL = urls.first else { return }
                    do {
                        let didAccess = fileURL.startAccessingSecurityScopedResource()
                        defer {
                            if didAccess {
                                fileURL.stopAccessingSecurityScopedResource()
                            }
                        }
                        try AppController.validateAudioFile(fileURL)
                        selectedAudioFileURL = fileURL
                        store.setError(nil)
                    } catch {
                        selectedAudioFileURL = nil
                        store.setError(error.localizedDescription)
                    }
                case let .failure(error):
                    store.setError(error.localizedDescription)
                }
            }
            .fileImporter(
                isPresented: $isCookieFileImporterPresented,
                allowedContentTypes: supportedCookieContentTypes,
                allowsMultipleSelection: false
            ) { result in
                switch result {
                case let .success(urls):
                    successMessage = nil
                    guard let fileURL = urls.first else { return }
                    selectedCookieFileURL = fileURL
                    store.setError(nil)
                case let .failure(error):
                    store.setError(error.localizedDescription)
                }
            }
            .overlay(alignment: .topTrailing) {
                BackendHeartbeatIndicator(status: store.backendHealthStatus)
                    .padding(.top, 16)
                    .padding(.trailing, 16)
                    .allowsHitTesting(false)
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
                        .accessibilityAddTraits(.isStaticText)
                } else if let successMessage {
                    Text(successMessage)
                        .font(.footnote)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .background(.green.opacity(0.9), in: Capsule())
                        .shadow(color: .black.opacity(0.1), radius: 4, y: 2)
                        .padding()
                        .accessibilityAddTraits(.isStaticText)
                }
            }
            .task {
                await ensureBackendRunning()
                await updateBackendHealth()
                startBackendHeartbeat()
                await controller.start(store: store)
                applyClipboardTextIfAvailable()
            }
            .onDisappear {
                stopBackendHeartbeat()
                cancelBackendStart()
            }
            .onOpenURL { url in
                handleOpenURL(url)
            }
            .onChange(of: statusMessage) { _, message in
                guard let message, message != announcedStatusMessage else { return }
                announcedStatusMessage = message
                NSAccessibility.post(element: NSApp, notification: .announcementRequested, userInfo: [.announcement: message])
            }
            .onChange(of: scenePhase) { _, phase in
                switch phase {
                case .active:
                    Task {
                        await ensureBackendRunning()
                        await updateBackendHealth()
                        startBackendHeartbeat()
                        applyClipboardTextIfAvailable()
                    }
                case .background:
                    stopBackendHeartbeat()
                    guard !store.hasActiveJobs, activeArtifactActionID == nil else { return }
                    cancelBackendStart()
                    backendProcess?.terminate()
                    backendProcess = nil
                    store.setBackendHealthStatus(.unhealthy)
                default:
                    break
                }
            }
            .onChange(of: store.jobs) { _, jobs in
                guard case let .job(jobID) = detailRoute else { return }
                if !jobs.contains(where: { $0.id == jobID }) {
                    detailRoute = .newTask
                }
            }
        }
    }
}
"""

with open("client/Apps/XDownloaderMac/XDownloaderMacApp.swift", "w") as f:
    f.write(new_content)

print("Patch applied successfully")
