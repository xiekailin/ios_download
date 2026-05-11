import Foundation
import Darwin

#if os(macOS)
import CryptoKit
public protocol BackendProcess: Sendable {
    func terminate()
}

private final class LoggedBackendProcess: BackendProcess, @unchecked Sendable {
    private let process: Process
    private let logHandle: FileHandle

    init(process: Process, logHandle: FileHandle) {
        self.process = process
        self.logHandle = logHandle
        self.process.terminationHandler = { _ in
            try? logHandle.close()
        }
    }

    func terminate() {
        process.terminate()
        try? logHandle.close()
    }
}

extension Process: BackendProcess {}

public enum LocalBackendLauncherError: LocalizedError {
    case healthCheckTimedOut
    case portOccupied

    public var errorDescription: String? {
        switch self {
        case .healthCheckTimedOut:
            "后端启动超时。"
        case .portOccupied:
            "本地端口 18767 已被其他服务占用。"
        }
    }
}

private actor BackendStartupGate {
    private var inFlight = [BackendStartupKey: Task<BackendProcess?, Error>]()

    func run(key: BackendStartupKey, _ operation: @escaping @Sendable () async throws -> BackendProcess?) async throws -> BackendProcess? {
        if let task = inFlight[key] {
            return try await task.value
        }
        let task = Task { try await operation() }
        inFlight[key] = task
        do {
            let result = try await task.value
            inFlight[key] = nil
            return result
        } catch {
            inFlight[key] = nil
            throw error
        }
    }
}

private struct BackendStartupKey: Hashable, Sendable {
    let healthURL: String
    let localSecret: String
    let expectedAppName: String
    let expectedYouTubeCookiesDisabled: Bool
    let expectedYouTubeCookiesFromBrowser: String?
}

public struct LocalBackendLauncher: Sendable {
    public typealias ProcessFactory = @Sendable (_ executableURL: URL, _ arguments: [String], _ environment: [String: String]) throws -> BackendProcess

    private let healthURL: URL
    private let executableURL: URL
    private let arguments: [String]
    private let environment: [String: String]
    private let session: URLSession
    private let processFactory: ProcessFactory
    private let expectedAppName: String
    private let expectedYouTubeCookiesDisabled: Bool
    private let expectedYouTubeCookiesFromBrowser: String?
    private let startupAttempts: Int
    public let localSecret: String
    private let startupRetryDelay: Duration
    private let startupTimeout: Duration

    private static let startupGate = BackendStartupGate()
    private static let startupProbeAttempts = 3

    public static let defaultStartupAttempts = 300
    public static let defaultStartupRetryDelay: Duration = .milliseconds(200)
    public static let defaultStartupTimeout: Duration = .seconds(60)

    public init(
        healthURL: URL = URL(string: "http://127.0.0.1:18767/api/v1/health")!,
        executableURL: URL = Self.defaultExecutableURL(),
        arguments: [String] = Self.defaultArguments(),
        environment: [String: String] = Self.defaultEnvironment(),
        session: URLSession = .shared,
        expectedAppName: String = "X Downloader API",
        localSecret: String = Self.makeLocalSecret(),
        startupAttempts: Int = Self.defaultStartupAttempts,
        startupRetryDelay: Duration = Self.defaultStartupRetryDelay,
        startupTimeout: Duration = Self.defaultStartupTimeout,
        processFactory: @escaping ProcessFactory = { executableURL, arguments, environment in
            try LocalBackendLauncher.startProcess(executableURL: executableURL, arguments: arguments, environment: environment)
        }
    ) {
        self.healthURL = healthURL
        self.executableURL = executableURL
        self.arguments = arguments
        self.environment = environment
        self.session = session
        self.expectedAppName = expectedAppName
        self.expectedYouTubeCookiesDisabled = environment["XDL_YOUTUBE_COOKIES_DISABLED"] == "1"
        self.expectedYouTubeCookiesFromBrowser = environment["XDL_YOUTUBE_COOKIES_FROM_BROWSER"]
        self.localSecret = localSecret
        self.startupAttempts = startupAttempts
        self.startupRetryDelay = startupRetryDelay
        self.startupTimeout = startupTimeout
        self.processFactory = processFactory
    }

    public func checkHealth() async -> BackendHealthStatus {
        switch await healthState() {
        case .matching:
            .healthy
        case .mismatchedConfiguration, .unavailable, .occupiedByOtherService:
            .unhealthy
        }
    }

    public func startAndCheckHealth() async throws -> BackendHealthStatus {
        _ = try await startIfNeeded()
        return await checkHealth()
    }

    public func restartAndCheckHealth() async throws -> BackendHealthStatus {
        await requestShutdown()
        try await waitForCurrentBackendToShutdown()
        _ = try await startIfNeeded()
        return await checkHealth()
    }

    public func startIfNeeded() async throws -> BackendProcess? {
        try await Self.startupGate.run(key: startupKey) {
            try await startIfNeededWithoutGate()
        }
    }

    private var startupKey: BackendStartupKey {
        BackendStartupKey(
            healthURL: healthURL.absoluteString,
            localSecret: localSecret,
            expectedAppName: expectedAppName,
            expectedYouTubeCookiesDisabled: expectedYouTubeCookiesDisabled,
            expectedYouTubeCookiesFromBrowser: expectedYouTubeCookiesFromBrowser
        )
    }

    private func startIfNeededWithoutGate() async throws -> BackendProcess? {
        switch try await stableStartupHealthState() {
        case .matching:
            return nil
        case .mismatchedConfiguration:
            await requestShutdown()
            switch try await waitForCurrentBackendToExitOrRecover() {
            case .matching:
                return nil
            case .occupiedByOtherService:
                throw LocalBackendLauncherError.portOccupied
            case .mismatchedConfiguration:
                throw LocalBackendLauncherError.healthCheckTimedOut
            case .unavailable:
                break
            }
        case .occupiedByOtherService:
            throw LocalBackendLauncherError.portOccupied
        case .unavailable:
            break
        }
        var launchEnvironment = environment
        launchEnvironment["XDL_LOCAL_SECRET"] = localSecret
        let process = try processFactory(executableURL, arguments, launchEnvironment)
        do {
            let start = ContinuousClock.now
            for _ in 0..<startupAttempts {
                try Task.checkCancellation()
                if await healthState() == .matching {
                    return process
                }
                if start.duration(to: .now) >= startupTimeout {
                    break
                }
                try await Task.sleep(for: startupRetryDelay)
            }
            process.terminate()
            throw LocalBackendLauncherError.healthCheckTimedOut
        } catch {
            process.terminate()
            throw error
        }
    }

    private func stableStartupHealthState() async throws -> HealthState {
        var lastState = await healthState()
        for _ in 1..<Self.startupProbeAttempts {
            if lastState != .unavailable {
                return lastState
            }
            try Task.checkCancellation()
            try await Task.sleep(for: startupRetryDelay)
            lastState = await healthState()
        }
        return lastState
    }

    private func healthState() async -> HealthState {
        let nonce = UUID().uuidString
        var request = URLRequest(url: healthURL.appending(queryItems: [URLQueryItem(name: "nonce", value: nonce)]))
        request.timeoutInterval = 1
        do {
            let (data, response) = try await session.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse else {
                return .unavailable
            }
            guard httpResponse.statusCode == 200 else {
                return .occupiedByOtherService
            }
            let health: HealthEnvelope
            do {
                health = try JSONDecoder().decode(HealthEnvelope.self, from: data)
            } catch {
                return .occupiedByOtherService
            }
            guard health.data.status == "ok",
                  health.data.appName == expectedAppName else {
                return .occupiedByOtherService
            }
            guard health.data.localProof == Self.localProof(secret: localSecret, nonce: nonce) else {
                return .occupiedByOtherService
            }
            guard let youtubeCookiesDisabled = health.data.youtubeCookiesDisabled else {
                return .mismatchedConfiguration
            }
            guard youtubeCookiesDisabled == expectedYouTubeCookiesDisabled,
                  health.data.youtubeCookiesFromBrowser == expectedYouTubeCookiesFromBrowser else {
                return .mismatchedConfiguration
            }
            return .matching
        } catch {
            return .unavailable
        }
    }

    private func waitForCurrentBackendToExitOrRecover() async throws -> HealthState {
        let start = ContinuousClock.now
        var consecutiveUnavailable = 0
        while start.duration(to: .now) < startupTimeout {
            try Task.checkCancellation()
            let state = await healthState()
            switch state {
            case .matching, .occupiedByOtherService:
                return state
            case .unavailable:
                consecutiveUnavailable += 1
                if consecutiveUnavailable >= Self.startupProbeAttempts {
                    return .unavailable
                }
            case .mismatchedConfiguration:
                consecutiveUnavailable = 0
            }
            try await Task.sleep(for: startupRetryDelay)
        }
        throw LocalBackendLauncherError.healthCheckTimedOut
    }

    private func waitForCurrentBackendToShutdown() async throws {
        let start = ContinuousClock.now
        var consecutiveUnavailable = 0
        while start.duration(to: .now) < startupTimeout {
            try Task.checkCancellation()
            switch await healthState() {
            case .unavailable:
                consecutiveUnavailable += 1
                if consecutiveUnavailable >= Self.startupProbeAttempts {
                    return
                }
            case .occupiedByOtherService:
                throw LocalBackendLauncherError.portOccupied
            case .matching, .mismatchedConfiguration:
                consecutiveUnavailable = 0
            }
            try await Task.sleep(for: startupRetryDelay)
        }
        throw LocalBackendLauncherError.healthCheckTimedOut
    }

    private func requestShutdown() async {
        let nonce = UUID().uuidString
        let shutdownURL = healthURL.appending(path: "shutdown").appending(queryItems: [URLQueryItem(name: "nonce", value: nonce)])
        var request = URLRequest(url: shutdownURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 1
        request.setValue(Self.localProof(secret: localSecret, nonce: nonce), forHTTPHeaderField: "X-XDownloader-Local-Proof")
        request.setValue(localSecret, forHTTPHeaderField: "X-XDownloader-Local-Secret")
        _ = try? await session.data(for: request)
    }

    public static func makeLocalSecret() -> String {
        UUID().uuidString + UUID().uuidString
    }

    public static func localProof(secret: String, nonce: String) -> String {
        let key = SymmetricKey(data: Data(secret.utf8))
        let signature = HMAC<SHA256>.authenticationCode(for: Data(nonce.utf8), using: key)
        return Data(signature).map { String(format: "%02x", $0) }.joined()
    }

    public static func defaultExecutableURL(resourceURL: URL? = Bundle.main.resourceURL) -> URL {
        if let backendURL = bundledBackendURL(resourceURL: resourceURL) {
            return backendURL
        }
        return defaultPythonURL()
    }

    public static func defaultPythonURL() -> URL {
        if let path = ProcessInfo.processInfo.environment["XDOWNLOADER_PYTHON"], !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        return URL(fileURLWithPath: "/opt/homebrew/bin/python3.12")
    }

    public static func defaultArguments(resourceURL: URL? = Bundle.main.resourceURL) -> [String] {
        if bundledBackendURL(resourceURL: resourceURL) != nil {
            return []
        }
        let serverDirectory = ProcessInfo.processInfo.environment["XDOWNLOADER_SERVER_DIR"]
            .map { URL(fileURLWithPath: $0) }
            ?? resourceURL?.appending(path: "server", directoryHint: .isDirectory)
            ?? URL(fileURLWithPath: "server", relativeTo: Bundle.main.bundleURL)
        return [
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", "18767",
            "--app-dir", serverDirectory.path,
        ]
    }

    public static func defaultEnvironment(
        resourceURL: URL? = Bundle.main.resourceURL,
        performanceSettings: DownloadPerformanceSettings = .balanced
    ) -> [String: String] {
        let currentEnvironment = ProcessInfo.processInfo.environment
        var environment = [String: String]()
        for key in ["HOME", "TMPDIR", "USER", "LOGNAME", "LANG", "LC_ALL", "XDOWNLOADER_PYTHON", "XDOWNLOADER_SERVER_DIR"] {
            environment[key] = currentEnvironment[key]
        }
        let bundledBinURL = resourceURL?.appending(path: "bin", directoryHint: .isDirectory)
        let bundledBinPath = bundledBinURL?.path
        let basePath = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        environment["PATH"] = [bundledBinPath, basePath].compactMap { $0 }.joined(separator: ":")
        if let ytDlpURL = bundledToolURL(named: "yt-dlp", resourceURL: resourceURL) {
            environment["XDL_YT_DLP_BINARY"] = ytDlpURL.path
        }
        if let ffmpegURL = bundledToolURL(named: "ffmpeg", resourceURL: resourceURL) {
            environment["XDL_FFMPEG_BINARY"] = ffmpegURL.path
        }
        let supportDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "XDownloader", directoryHint: .isDirectory)
        let artifactsDirectory = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
            .appending(path: "XDownloader", directoryHint: .isDirectory)
        environment["XDL_DATA_DIR"] = supportDirectory.path
        environment["XDL_DATABASE_PATH"] = supportDirectory.appending(path: "app.db").path
        environment["XDL_ARTIFACTS_DIR"] = artifactsDirectory.path
        environment["XDL_BACKEND_LOG_PATH"] = supportDirectory.appending(path: "backend.log").path
        environment["XDL_PERFORMANCE_MODE"] = currentEnvironment["XDL_PERFORMANCE_MODE"] ?? performanceSettings.performanceMode.backendValue
        environment["XDL_DOWNLOAD_WORKER_MAX_JOBS"] = currentEnvironment["XDL_DOWNLOAD_WORKER_MAX_JOBS"] ?? String(performanceSettings.simultaneousDownloadJobs)
        environment["XDL_AUDIO_SEPARATION_WORKER_MAX_JOBS"] = currentEnvironment["XDL_AUDIO_SEPARATION_WORKER_MAX_JOBS"] ?? "1"
        environment["XDL_YTDLP_CONCURRENT_FRAGMENTS"] = currentEnvironment["XDL_YTDLP_CONCURRENT_FRAGMENTS"] ?? String(performanceSettings.ytdlpConcurrentFragments)
        environment["XDL_YTDLP_FORMAT_STRATEGY"] = currentEnvironment["XDL_YTDLP_FORMAT_STRATEGY"] ?? "adaptive"
        environment["XDL_FFMPEG_THREADS"] = currentEnvironment["XDL_FFMPEG_THREADS"] ?? String(performanceSettings.ffmpegThreadCount)
        environment["XDL_YTDLP_EXTERNAL_DOWNLOADER"] = currentEnvironment["XDL_YTDLP_EXTERNAL_DOWNLOADER"] ?? "auto"
        environment["XDL_YTDLP_EXTERNAL_DOWNLOADER_ARGS"] = currentEnvironment["XDL_YTDLP_EXTERNAL_DOWNLOADER_ARGS"] ?? "aria2c:-x 8 -s 8 -k 1M"
        environment["XDL_DIRECT_DOWNLOAD_MAX_CONNECTIONS"] = currentEnvironment["XDL_DIRECT_DOWNLOAD_MAX_CONNECTIONS"] ?? String(performanceSettings.directDownloadMaxConnectionsForBackend)
        environment["XDL_DIRECT_DOWNLOAD_SEGMENT_MIN_BYTES"] = currentEnvironment["XDL_DIRECT_DOWNLOAD_SEGMENT_MIN_BYTES"] ?? String(performanceSettings.directDownloadSegmentMinBytes)
        environment["XDL_DIRECT_DOWNLOAD_SEGMENT_SIZE"] = currentEnvironment["XDL_DIRECT_DOWNLOAD_SEGMENT_SIZE"] ?? String(performanceSettings.directDownloadSegmentSizeBytes)
        environment["XDL_QUEUE_NIGHT_DOWNLOAD_ENABLED"] = currentEnvironment["XDL_QUEUE_NIGHT_DOWNLOAD_ENABLED"] ?? String(performanceSettings.nightDownloadEnabled)
        environment["XDL_QUEUE_NIGHT_START_HOUR"] = currentEnvironment["XDL_QUEUE_NIGHT_START_HOUR"] ?? String(performanceSettings.nightDownloadStartHour)
        environment["XDL_QUEUE_NIGHT_END_HOUR"] = currentEnvironment["XDL_QUEUE_NIGHT_END_HOUR"] ?? String(performanceSettings.nightDownloadEndHour)
        let downloadRateLimit = currentEnvironment["XDL_DOWNLOAD_RATE_LIMIT"]
            ?? performanceSettings.downloadRateLimit.trimmingCharacters(in: .whitespacesAndNewlines)
        if !downloadRateLimit.isEmpty {
            environment["XDL_DOWNLOAD_RATE_LIMIT"] = downloadRateLimit
        }
        environment["XDL_YOUTUBE_COOKIES_FROM_BROWSER"] = "chrome"
        environment["XDL_YOUTUBE_REMOTE_COMPONENTS"] = "ejs:github"
        if let demucsPythonURL = demucsPythonURL(supportDirectory: supportDirectory) {
            environment["XDL_AUDIO_SEPARATION_COMMAND"] = "\(shellQuoted(demucsPythonURL.path)) -m demucs --two-stems=vocals --filename {stem}.{ext} -o {output_dir:q} {input:q}"
        }
        return environment
    }

    public static func demucsPythonURL(supportDirectory: URL) -> URL? {
        let url = supportDirectory
            .appending(path: "demucs-venv", directoryHint: .isDirectory)
            .appending(path: "bin", directoryHint: .isDirectory)
            .appending(path: "python")
        guard FileManager.default.isExecutableFile(atPath: url.path) else { return nil }
        return url
    }

    public static func shellQuoted(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    public static func bundledBackendURL(resourceURL: URL? = Bundle.main.resourceURL) -> URL? {
        let backendDirectory = resourceURL?.appending(path: "backend", directoryHint: .isDirectory)
        let candidateURLs = [
            backendDirectory?
                .appending(path: "xdownloader-backend", directoryHint: .isDirectory)
                .appending(path: "xdownloader-backend"),
            backendDirectory?.appending(path: "xdownloader-backend"),
        ]
        return candidateURLs.compactMap { $0 }.first { FileManager.default.isExecutableFile(atPath: $0.path) }
    }

    public static func bundledToolURL(named name: String, resourceURL: URL? = Bundle.main.resourceURL) -> URL? {
        let url = resourceURL?
            .appending(path: "bin", directoryHint: .isDirectory)
            .appending(path: name)
        guard let url, FileManager.default.isExecutableFile(atPath: url.path) else { return nil }
        return url
    }

    private enum HealthState: Equatable {
        case matching
        case mismatchedConfiguration
        case unavailable
        case occupiedByOtherService
    }

    private struct HealthEnvelope: Decodable {
        struct Data: Decodable {
            let status: String
            let appName: String
            let localProof: String?
            let youtubeCookiesDisabled: Bool?
            let youtubeCookiesFromBrowser: String?

            private enum CodingKeys: String, CodingKey {
                case status
                case appName = "app_name"
                case localProof = "local_proof"
                case youtubeCookiesDisabled = "youtube_cookies_disabled"
                case youtubeCookiesFromBrowser = "youtube_cookies_from_browser"
            }
        }

        let data: Data
    }

    private static func openLogFile(at url: URL) throws -> FileHandle {
        var info = stat()
        if lstat(url.path, &info) == 0 {
            guard (info.st_mode & S_IFMT) == S_IFREG else {
                throw CocoaError(.fileWriteUnknown)
            }
        }
        let fd = open(url.path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard fd >= 0 else {
            throw CocoaError(.fileWriteUnknown)
        }
        var openedInfo = stat()
        guard fstat(fd, &openedInfo) == 0, (openedInfo.st_mode & S_IFMT) == S_IFREG, fchmod(fd, S_IRUSR | S_IWUSR) == 0 else {
            close(fd)
            throw CocoaError(.fileWriteUnknown)
        }
        return FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    }

    public static func startProcess(executableURL: URL, arguments: [String], environment: [String: String]) throws -> BackendProcess {
        let process = Process()
        process.executableURL = executableURL
        process.arguments = arguments
        var logHandle: FileHandle?
        if let logPath = environment["XDL_BACKEND_LOG_PATH"] {
            let logURL = URL(fileURLWithPath: logPath)
            try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            logHandle = try openLogFile(at: logURL)
            process.standardOutput = logHandle
            process.standardError = logHandle
        } else {
            process.standardOutput = nil
            process.standardError = nil
        }
        process.environment = environment
        do {
            try process.run()
        } catch {
            if let logHandle {
                let message = "failed to start backend: \(error.localizedDescription)\n"
                try? logHandle.write(contentsOf: Data(message.utf8))
            }
            try? logHandle?.close()
            throw error
        }
        if let logHandle {
            return LoggedBackendProcess(process: process, logHandle: logHandle)
        }
        return process
    }
}
#endif
