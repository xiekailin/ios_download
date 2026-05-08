import AppCore
import Foundation
import Testing

private final class MockBackendProcess: BackendProcess, @unchecked Sendable {
    private(set) var didTerminate = false

    func terminate() {
        didTerminate = true
    }
}

private final class ProcessStartRecorder: @unchecked Sendable {
    var count = 0
    var healthRequestsBeforeLaunch = 0
}

private final class BackendHealthProtocolStub: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: ((URLRequest) -> Data)?
    nonisolated(unsafe) static var responseStatusCode = 200
    nonisolated(unsafe) static var responseError: Error?
    nonisolated(unsafe) static var requests: [URLRequest] = []
    nonisolated(unsafe) static var healthRequestsBeforeLaunch = 0

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        Self.requests.append(request)
        let data = Self.handler?(request) ?? Data()
        if let responseError = Self.responseError {
            client?.urlProtocol(self, didFailWithError: responseError)
            return
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: Self.responseStatusCode, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

private func backendHealthSession() -> URLSession {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [BackendHealthProtocolStub.self]
    return URLSession(configuration: configuration)
}

private func startError(_ launcher: LocalBackendLauncher) async -> LocalBackendLauncherError? {
    do {
        _ = try await launcher.startIfNeeded()
        return nil
    } catch let error as LocalBackendLauncherError {
        return error
    } catch {
        return nil
    }
}

@Suite(.serialized)
struct LocalBackendLauncherTests {
@Test func localBackendLauncherStartsProcessWhenHealthCheckFails() async throws {
    let process = MockBackendProcess()
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        session: .shared,
        startupAttempts: 0,
        processFactory: { executableURL, arguments, environment in
            #expect(executableURL.path == "/bin/echo")
            #expect(arguments == ["ok"])
            #expect(environment["PATH"] != nil)
            return process
        }
    )

    await #expect(throws: LocalBackendLauncherError.healthCheckTimedOut) {
        _ = try await launcher.startIfNeeded()
    }
    #expect(process.didTerminate)
}

@Test func localBackendLauncherPassesDefaultPort18767ToProcess() async throws {
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        session: .shared,
        startupAttempts: 0,
        processFactory: { _, arguments, environment in
            #expect(arguments.contains("18767"))
            #expect(environment["XDL_DATABASE_PATH"]?.hasSuffix("app.db") == true)
            #expect(environment["XDL_DATABASE_PATH"]?.contains("Application Support") == true)
            #expect(environment["XDL_ARTIFACTS_DIR"]?.hasSuffix("Downloads/XDownloader") == true)
            #expect(environment["XDL_ARTIFACTS_DIR"]?.contains("Application Support") == false)
            return MockBackendProcess()
        }
    )

    await #expect(throws: LocalBackendLauncherError.healthCheckTimedOut) {
        _ = try await launcher.startIfNeeded()
    }
}

@Test func localBackendLauncherPrefersBundledBackendExecutable() throws {
    let resources = FileManager.default.temporaryDirectory.appending(path: "xdl-resources-\(UUID().uuidString)", directoryHint: .isDirectory)
    let backendDirectory = resources.appending(path: "backend", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: backendDirectory, withIntermediateDirectories: true)
    let backendURL = backendDirectory.appending(path: "xdownloader-backend")
    try Data().write(to: backendURL)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: backendURL.path)
    defer { try? FileManager.default.removeItem(at: resources) }

    #expect(LocalBackendLauncher.defaultExecutableURL(resourceURL: resources) == backendURL)
    #expect(LocalBackendLauncher.defaultArguments(resourceURL: resources).isEmpty)
}

@Test func localBackendLauncherPrefersBundledBackendOnedirExecutable() throws {
    let resources = FileManager.default.temporaryDirectory.appending(path: "xdl-resources-\(UUID().uuidString)", directoryHint: .isDirectory)
    let backendDirectory = resources.appending(path: "backend", directoryHint: .isDirectory)
    let bundleDirectory = backendDirectory.appending(path: "xdownloader-backend", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: bundleDirectory, withIntermediateDirectories: true)
    let backendURL = bundleDirectory.appending(path: "xdownloader-backend")
    try Data().write(to: backendURL)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: backendURL.path)
    defer { try? FileManager.default.removeItem(at: resources) }

    #expect(LocalBackendLauncher.defaultExecutableURL(resourceURL: resources) == backendURL)
    #expect(LocalBackendLauncher.defaultArguments(resourceURL: resources).isEmpty)
}

@Test func localBackendLauncherInjectsBundledToolPaths() throws {
    let resources = FileManager.default.temporaryDirectory.appending(path: "xdl-tools-\(UUID().uuidString)", directoryHint: .isDirectory)
    let binDirectory = resources.appending(path: "bin", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: binDirectory, withIntermediateDirectories: true)
    let ytDlpURL = binDirectory.appending(path: "yt-dlp")
    let ffmpegURL = binDirectory.appending(path: "ffmpeg")
    try Data().write(to: ytDlpURL)
    try Data().write(to: ffmpegURL)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: ytDlpURL.path)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: ffmpegURL.path)
    defer { try? FileManager.default.removeItem(at: resources) }

    let environment = LocalBackendLauncher.defaultEnvironment(resourceURL: resources)

    #expect(environment["XDL_YT_DLP_BINARY"] == ytDlpURL.path)
    #expect(environment["XDL_FFMPEG_BINARY"] == ffmpegURL.path)
    #expect(environment["PATH"]?.hasPrefix(binDirectory.path + ":") == true)
    #expect(environment["XDL_YOUTUBE_COOKIES_FROM_BROWSER"] == "chrome")
    #expect(environment["XDL_YOUTUBE_COOKIES_DISABLED"] == nil)
    #expect(environment["XDL_YOUTUBE_REMOTE_COMPONENTS"] == "ejs:github")
}

@Test func localBackendLauncherAllowsYouTubeCookieRetryByDefault() {
    let environment = LocalBackendLauncher.defaultEnvironment()

    #expect(environment["XDL_YOUTUBE_COOKIES_FROM_BROWSER"] == "chrome")
    #expect(environment["XDL_YOUTUBE_COOKIES_DISABLED"] == nil)
    #expect(environment["XDL_YOUTUBE_REMOTE_COMPONENTS"] == "ejs:github")
}

@Test func localBackendLauncherKeepsCookieRetryEnabledWhenRequested() {
    let environment = LocalBackendLauncher.defaultEnvironment()

    #expect(environment["XDL_YOUTUBE_COOKIES_FROM_BROWSER"] == "chrome")
    #expect(environment["XDL_YOUTUBE_COOKIES_DISABLED"] == nil)
    #expect(environment["XDL_YOUTUBE_REMOTE_COMPONENTS"] == "ejs:github")
}

@Test func localBackendLauncherReportsHealthyWhenHealthMatchesExpectedState() async throws {
    let secret = "secret-1"
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.handler = { request in
        let nonce = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "nonce" })?.value ?? ""
        let proof = LocalBackendLauncher.localProof(secret: secret, nonce: nonce)
        return Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"\#(proof)","youtube_cookies_from_browser":"chrome","youtube_cookies_disabled":false}}"#.utf8)
    }
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        environment: LocalBackendLauncher.defaultEnvironment(),
        session: backendHealthSession(),
        localSecret: secret
    )

    #expect(await launcher.checkHealth() == .healthy)
}

@Test func localBackendLauncherReportsUnhealthyWhenHealthIsInvalid() async throws {
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.handler = { _ in Data(#"{"data":{"status":"ok"}}"#.utf8) }
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        session: backendHealthSession(),
        localSecret: "secret-1"
    )

    #expect(await launcher.checkHealth() == .unhealthy)
}

@Test func localBackendLauncherReportsUnhealthyWhenCookieStateIsDisabled() async throws {
    let secret = "secret-1"
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.handler = { request in
        let nonce = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "nonce" })?.value ?? ""
        let proof = LocalBackendLauncher.localProof(secret: secret, nonce: nonce)
        return Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"\#(proof)","youtube_cookies_from_browser":null,"youtube_cookies_disabled":true}}"#.utf8)
    }
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        environment: LocalBackendLauncher.defaultEnvironment(),
        session: backendHealthSession(),
        localSecret: secret
    )

    #expect(await launcher.checkHealth() == .unhealthy)
}

@Test func localBackendLauncherStartAndCheckHealthReturnsHealthyAfterStartingBundledBackend() async throws {
    let secret = "secret-1"
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.responseStatusCode = 200
    BackendHealthProtocolStub.responseError = URLError(.cannotConnectToHost)
    var healthChecks = 0
    let startRecorder = ProcessStartRecorder()
    BackendHealthProtocolStub.handler = { request in
        healthChecks += 1
        guard healthChecks > 3 else {
            BackendHealthProtocolStub.responseError = URLError(.cannotConnectToHost)
            return Data()
        }
        BackendHealthProtocolStub.responseError = nil
        let nonce = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "nonce" })?.value ?? ""
        let proof = LocalBackendLauncher.localProof(secret: secret, nonce: nonce)
        return Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"\#(proof)","youtube_cookies_from_browser":"chrome","youtube_cookies_disabled":false}}"#.utf8)
    }
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        environment: LocalBackendLauncher.defaultEnvironment(),
        session: backendHealthSession(),
        localSecret: secret,
        startupAttempts: 3,
        startupRetryDelay: .milliseconds(1),
        startupTimeout: .milliseconds(50),
        processFactory: { _, _, _ in
            startRecorder.count += 1
            return MockBackendProcess()
        }
    )

    let status = try await launcher.startAndCheckHealth()

    #expect(status == .healthy)
    #expect(startRecorder.count == 1)
    BackendHealthProtocolStub.responseError = nil
}

@Test func localBackendLauncherReusesBackendWhenSecondHealthCheckMatches() async throws {
    let secret = "secret-1"
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.responseStatusCode = 200
    BackendHealthProtocolStub.responseError = URLError(.cannotConnectToHost)
    var healthChecks = 0
    let startRecorder = ProcessStartRecorder()
    BackendHealthProtocolStub.handler = { request in
        guard request.url?.path == "/api/v1/health" else {
            return Data(#"{"data":{"ok":true}}"#.utf8)
        }
        healthChecks += 1
        guard healthChecks > 1 else {
            return Data()
        }
        BackendHealthProtocolStub.responseError = nil
        let nonce = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "nonce" })?.value ?? ""
        let proof = LocalBackendLauncher.localProof(secret: secret, nonce: nonce)
        return Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"\#(proof)","youtube_cookies_from_browser":"chrome","youtube_cookies_disabled":false}}"#.utf8)
    }
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        environment: LocalBackendLauncher.defaultEnvironment(),
        session: backendHealthSession(),
        localSecret: secret,
        startupRetryDelay: .milliseconds(1),
        processFactory: { _, _, _ in
            startRecorder.count += 1
            return MockBackendProcess()
        }
    )

    let process = try await launcher.startIfNeeded()

    #expect(process == nil)
    #expect(startRecorder.count == 0)
    BackendHealthProtocolStub.responseError = nil
    BackendHealthProtocolStub.responseStatusCode = 200
}

@Test func localBackendLauncherDoesNotStartWhenPortIsOccupiedByOtherService() async throws {
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.responseStatusCode = 200
    let startRecorder = ProcessStartRecorder()
    BackendHealthProtocolStub.handler = { _ in Data(#"{"data":{"status":"ok","app_name":"Other Service"}}"#.utf8) }
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        session: backendHealthSession(),
        startupRetryDelay: .milliseconds(1),
        processFactory: { _, _, _ in
            startRecorder.count += 1
            return MockBackendProcess()
        }
    )

    await #expect(throws: LocalBackendLauncherError.portOccupied) {
        _ = try await launcher.startIfNeeded()
    }
    #expect(startRecorder.count == 0)
    #expect(!BackendHealthProtocolStub.requests.contains { $0.url?.path == "/api/v1/health/shutdown" })
}

@Test func localBackendLauncherDoesNotStartWhenPortRespondsWithNonHealthyStatus() async throws {
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.responseStatusCode = 503
    BackendHealthProtocolStub.handler = { _ in Data() }
    let startRecorder = ProcessStartRecorder()
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        session: backendHealthSession(),
        startupRetryDelay: .milliseconds(1),
        processFactory: { _, _, _ in
            startRecorder.count += 1
            return MockBackendProcess()
        }
    )

    await #expect(throws: LocalBackendLauncherError.portOccupied) {
        _ = try await launcher.startIfNeeded()
    }
    #expect(startRecorder.count == 0)
    BackendHealthProtocolStub.responseStatusCode = 200
}

@Test func localBackendLauncherDoesNotReuseBackendWithDisabledCookieState() async throws {
    let secret = "secret-1"
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.handler = { request in
        let nonce = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "nonce" })?.value ?? ""
        let proof = LocalBackendLauncher.localProof(secret: secret, nonce: nonce)
        return Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"\#(proof)","youtube_cookies_from_browser":null,"youtube_cookies_disabled":true}}"#.utf8)
    }
    let process = MockBackendProcess()
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        environment: LocalBackendLauncher.defaultEnvironment(),
        session: backendHealthSession(),
        localSecret: secret,
        startupAttempts: 1,
        startupRetryDelay: .milliseconds(1),
        startupTimeout: .milliseconds(10),
        processFactory: { _, _, _ in process }
    )

    await #expect(throws: LocalBackendLauncherError.healthCheckTimedOut) {
        _ = try await launcher.startIfNeeded()
    }
    #expect(BackendHealthProtocolStub.requests.contains { $0.url?.path == "/api/v1/health/shutdown" })
    #expect(!process.didTerminate)
}

@Test func localBackendLauncherDoesNotStartReplacementWhenBackendProofMismatches() async throws {
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.responseStatusCode = 200
    BackendHealthProtocolStub.handler = { _ in
        Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"stale-proof","youtube_cookies_from_browser":null,"youtube_cookies_disabled":true}}"#.utf8)
    }
    let startRecorder = ProcessStartRecorder()
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        session: backendHealthSession(),
        localSecret: "new-secret",
        startupRetryDelay: .milliseconds(1),
        startupTimeout: .milliseconds(10),
        processFactory: { _, _, _ in
            startRecorder.count += 1
            return MockBackendProcess()
        }
    )

    await #expect(throws: LocalBackendLauncherError.portOccupied) {
        _ = try await launcher.startIfNeeded()
    }
    #expect(startRecorder.count == 0)
    #expect(!BackendHealthProtocolStub.requests.contains { $0.url?.path == "/api/v1/health/shutdown" })
    #expect(!BackendHealthProtocolStub.requests.contains { $0.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") != nil })
}

@Test func localBackendLauncherWaitsForMismatchedBackendToExitBeforeStartingReplacement() async throws {
    let secret = "secret-1"
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.responseStatusCode = 200
    BackendHealthProtocolStub.healthRequestsBeforeLaunch = 0
    BackendHealthProtocolStub.handler = { request in
        guard request.url?.path == "/api/v1/health" else {
            return Data(#"{"data":{"ok":true}}"#.utf8)
        }
        let healthCount = BackendHealthProtocolStub.requests.filter { $0.url?.path == "/api/v1/health" }.count
        guard healthCount < 3 else {
            BackendHealthProtocolStub.responseError = URLError(.cannotConnectToHost)
            return Data()
        }
        let nonce = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "nonce" })?.value ?? ""
        let proof = LocalBackendLauncher.localProof(secret: secret, nonce: nonce)
        return Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"\#(proof)","youtube_cookies_from_browser":null,"youtube_cookies_disabled":true}}"#.utf8)
    }
    let process = MockBackendProcess()
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        environment: LocalBackendLauncher.defaultEnvironment(),
        session: backendHealthSession(),
        localSecret: secret,
        startupAttempts: 1,
        startupRetryDelay: .milliseconds(1),
        startupTimeout: .milliseconds(50),
        processFactory: { _, _, _ in
            BackendHealthProtocolStub.healthRequestsBeforeLaunch = BackendHealthProtocolStub.requests.filter { $0.url?.path == "/api/v1/health" }.count
            return process
        }
    )

    await #expect(throws: LocalBackendLauncherError.healthCheckTimedOut) {
        _ = try await launcher.startIfNeeded()
    }
    #expect(BackendHealthProtocolStub.requests.contains { $0.url?.path == "/api/v1/health/shutdown" })
    #expect(BackendHealthProtocolStub.healthRequestsBeforeLaunch >= 3)
    #expect(process.didTerminate)
    BackendHealthProtocolStub.responseError = nil
    BackendHealthProtocolStub.responseStatusCode = 200
}

@Test func localBackendLauncherWaitsForRepeatedUnavailableStatesBeforeStartingReplacement() async throws {
    let secret = "secret-1"
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.responseStatusCode = 200
    var healthChecks = 0
    let startRecorder = ProcessStartRecorder()
    let process = MockBackendProcess()
    BackendHealthProtocolStub.handler = { request in
        guard request.url?.path == "/api/v1/health" else {
            return Data(#"{"data":{"ok":true}}"#.utf8)
        }
        healthChecks += 1
        if healthChecks == 1 {
            let nonce = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "nonce" })?.value ?? ""
            let proof = LocalBackendLauncher.localProof(secret: secret, nonce: nonce)
            return Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"\#(proof)","youtube_cookies_from_browser":null,"youtube_cookies_disabled":true}}"#.utf8)
        }
        BackendHealthProtocolStub.responseError = URLError(.cannotConnectToHost)
        return Data()
    }
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        environment: LocalBackendLauncher.defaultEnvironment(),
        session: backendHealthSession(),
        localSecret: secret,
        startupAttempts: 0,
        startupRetryDelay: .milliseconds(1),
        startupTimeout: .milliseconds(50),
        processFactory: { _, _, _ in
            startRecorder.healthRequestsBeforeLaunch = BackendHealthProtocolStub.requests.filter { $0.url?.path == "/api/v1/health" }.count
            return process
        }
    )

    await #expect(throws: LocalBackendLauncherError.healthCheckTimedOut) {
        _ = try await launcher.startIfNeeded()
    }

    #expect(startRecorder.healthRequestsBeforeLaunch >= 4)
    #expect(process.didTerminate)
    BackendHealthProtocolStub.responseError = nil
    BackendHealthProtocolStub.responseStatusCode = 200
}

@Test func localBackendLauncherDoesNotStartReplacementWhenMismatchedBackendBecomesHealthy() async throws {
    let secret = "secret-1"
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.responseStatusCode = 200
    var healthChecks = 0
    let startRecorder = ProcessStartRecorder()
    BackendHealthProtocolStub.handler = { request in
        guard request.url?.path == "/api/v1/health" else {
            return Data(#"{"data":{"ok":true}}"#.utf8)
        }
        healthChecks += 1
        let nonce = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "nonce" })?.value ?? ""
        let proof = LocalBackendLauncher.localProof(secret: secret, nonce: nonce)
        if healthChecks == 1 {
            return Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"\#(proof)","youtube_cookies_from_browser":null,"youtube_cookies_disabled":true}}"#.utf8)
        }
        return Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"\#(proof)","youtube_cookies_from_browser":"chrome","youtube_cookies_disabled":false}}"#.utf8)
    }
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        environment: LocalBackendLauncher.defaultEnvironment(),
        session: backendHealthSession(),
        localSecret: secret,
        startupRetryDelay: .milliseconds(1),
        startupTimeout: .milliseconds(10),
        processFactory: { _, _, _ in
            startRecorder.count += 1
            return MockBackendProcess()
        }
    )

    let process = try await launcher.startIfNeeded()

    #expect(process == nil)
    #expect(startRecorder.count == 0)
    #expect(BackendHealthProtocolStub.requests.contains { $0.url?.path == "/api/v1/health/shutdown" })
}

@Test func localBackendLauncherDoesNotReuseBackendWithMissingCookieState() async throws {
    let secret = "secret-1"
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.handler = { request in
        let nonce = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "nonce" })?.value ?? ""
        let proof = LocalBackendLauncher.localProof(secret: secret, nonce: nonce)
        return Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"\#(proof)","youtube_cookies_from_browser":"chrome"}}"#.utf8)
    }
    let process = MockBackendProcess()
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        environment: LocalBackendLauncher.defaultEnvironment(),
        session: backendHealthSession(),
        localSecret: secret,
        startupAttempts: 1,
        startupRetryDelay: .milliseconds(1),
        startupTimeout: .milliseconds(10),
        processFactory: { _, _, _ in process }
    )

    await #expect(throws: LocalBackendLauncherError.healthCheckTimedOut) {
        _ = try await launcher.startIfNeeded()
    }
    #expect(BackendHealthProtocolStub.requests.contains { $0.url?.path == "/api/v1/health/shutdown" })
    #expect(!process.didTerminate)
}

@Test func localBackendLauncherDoesNotReuseBackendWithMismatchedCookieSource() async throws {
    let secret = "secret-1"
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.handler = { request in
        let nonce = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "nonce" })?.value ?? ""
        let proof = LocalBackendLauncher.localProof(secret: secret, nonce: nonce)
        return Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"\#(proof)","youtube_cookies_disabled":false}}"#.utf8)
    }
    let process = MockBackendProcess()
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        environment: LocalBackendLauncher.defaultEnvironment(),
        session: backendHealthSession(),
        localSecret: secret,
        startupAttempts: 1,
        startupRetryDelay: .milliseconds(1),
        startupTimeout: .milliseconds(10),
        processFactory: { _, _, _ in process }
    )

    await #expect(throws: LocalBackendLauncherError.healthCheckTimedOut) {
        _ = try await launcher.startIfNeeded()
    }
    #expect(BackendHealthProtocolStub.requests.contains { $0.url?.path == "/api/v1/health/shutdown" })
    #expect(!process.didTerminate)
}

@Test func localBackendLauncherStartsOnlyOnceForConcurrentStartIfNeededCalls() async throws {
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.responseStatusCode = 200
    BackendHealthProtocolStub.responseError = URLError(.cannotConnectToHost)
    BackendHealthProtocolStub.handler = { _ in Data() }
    let startRecorder = ProcessStartRecorder()
    let process = MockBackendProcess()
    let launcher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        executableURL: URL(fileURLWithPath: "/bin/echo"),
        arguments: ["ok"],
        session: backendHealthSession(),
        startupAttempts: 0,
        startupRetryDelay: .milliseconds(1),
        processFactory: { _, _, _ in
            startRecorder.count += 1
            return process
        }
    )

    async let first = startError(launcher)
    async let second = startError(launcher)
    let errors = await [first, second]

    #expect(errors == [.healthCheckTimedOut, .healthCheckTimedOut])
    #expect(startRecorder.count == 1)
    BackendHealthProtocolStub.responseError = nil
    BackendHealthProtocolStub.responseStatusCode = 200
}

@Test func localBackendLauncherRechecksDifferentSecretsDuringConcurrentStarts() async throws {
    let trustedSecret = "secret-1"
    BackendHealthProtocolStub.requests = []
    BackendHealthProtocolStub.responseStatusCode = 200
    var healthChecks = 0
    BackendHealthProtocolStub.handler = { request in
        healthChecks += 1
        if healthChecks == 1 {
            Thread.sleep(forTimeInterval: 0.05)
        }
        let nonce = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "nonce" })?.value ?? ""
        let proof = LocalBackendLauncher.localProof(secret: trustedSecret, nonce: nonce)
        return Data(#"{"data":{"status":"ok","app_name":"X Downloader API","local_proof":"\#(proof)","youtube_cookies_from_browser":"chrome","youtube_cookies_disabled":false}}"#.utf8)
    }
    let trustedLauncher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        environment: LocalBackendLauncher.defaultEnvironment(),
        session: backendHealthSession(),
        localSecret: trustedSecret
    )
    let untrustedLauncher = LocalBackendLauncher(
        healthURL: URL(string: "http://127.0.0.1:1/api/v1/health")!,
        environment: LocalBackendLauncher.defaultEnvironment(),
        session: backendHealthSession(),
        localSecret: "secret-2"
    )

    async let first = startError(trustedLauncher)
    try await Task.sleep(for: .milliseconds(10))
    async let second = startError(untrustedLauncher)
    let errors = await [first, second]

    #expect(errors == [nil, .portOccupied])
    #expect(BackendHealthProtocolStub.requests.filter { $0.url?.path == "/api/v1/health" }.count >= 2)
    #expect(!BackendHealthProtocolStub.requests.contains { $0.url?.path == "/api/v1/health/shutdown" })
}

@Test func localBackendLauncherDetectsDemucsPython() throws {
    let supportDirectory = FileManager.default.temporaryDirectory.appending(path: "xdl-demucs-\(UUID().uuidString)", directoryHint: .isDirectory)
    let binDirectory = supportDirectory.appending(path: "demucs-venv/bin", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: binDirectory, withIntermediateDirectories: true)
    let pythonURL = binDirectory.appending(path: "python")
    try Data().write(to: pythonURL)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: pythonURL.path)
    defer { try? FileManager.default.removeItem(at: supportDirectory) }

    #expect(LocalBackendLauncher.demucsPythonURL(supportDirectory: supportDirectory) == pythonURL)
}

@Test func localBackendLauncherShellQuotesPaths() {
    #expect(LocalBackendLauncher.shellQuoted("/tmp/Application Support/python") == "'/tmp/Application Support/python'")
    #expect(LocalBackendLauncher.shellQuoted("/tmp/a'b/python") == "'/tmp/a'\\''b/python'")
}

@Test func localBackendLauncherDefaultStartupWindowAllowsSlowFirstLaunch() {
    #expect(LocalBackendLauncher.defaultStartupAttempts == 300)
    #expect(LocalBackendLauncher.defaultStartupRetryDelay == .milliseconds(200))
    #expect(LocalBackendLauncher.defaultStartupTimeout == .seconds(60))
}

@Test func localBackendLauncherWritesBackendOutputToPrivateLogFile() async throws {
    let directory = FileManager.default.temporaryDirectory.appending(path: "xdl-log-\(UUID().uuidString)", directoryHint: .isDirectory)
    let logURL = directory.appending(path: "backend.log")
    defer { try? FileManager.default.removeItem(at: directory) }

    let process = try LocalBackendLauncher.startProcess(
        executableURL: URL(fileURLWithPath: "/bin/sh"),
        arguments: ["-c", "echo backend-out; echo backend-err >&2; sleep 1"],
        environment: ["XDL_BACKEND_LOG_PATH": logURL.path]
    )
    try await Task.sleep(for: .milliseconds(100))
    process.terminate()
    try await Task.sleep(for: .milliseconds(100))

    let log = try String(contentsOf: logURL, encoding: .utf8)
    let attributes = try FileManager.default.attributesOfItem(atPath: logURL.path)

    #expect(log.contains("backend-out"))
    #expect(log.contains("backend-err"))
    #expect(attributes[.posixPermissions] as? Int == 0o600)
}

@Test func localBackendLauncherInjectsBackendLogPath() {
    let environment = LocalBackendLauncher.defaultEnvironment()

    #expect(environment["XDL_BACKEND_LOG_PATH"]?.contains("Application Support") == true)
    #expect(environment["XDL_BACKEND_LOG_PATH"]?.hasSuffix("backend.log") == true)
}

@Test func localBackendLauncherDefaultsToBalancedResourceProfile() {
    let environment = LocalBackendLauncher.defaultEnvironment()

    #expect(environment["XDL_PERFORMANCE_MODE"] == "balanced")
    #expect(environment["XDL_DOWNLOAD_WORKER_MAX_JOBS"] == "2")
    #expect(environment["XDL_AUDIO_SEPARATION_WORKER_MAX_JOBS"] == "1")
}
}
