import AppCore
import Foundation

private extension Data {
    mutating func append(_ string: String) {
        append(Data(string.utf8))
    }
}

public enum APIClientError: LocalizedError {
    case invalidResponse
    case server(String)

    public var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "服务端响应格式不正确。"
        case let .server(message):
            return message
        }
    }
}

public struct APIClient: Sendable, ClientAPI {
    public let baseURL: URL
    public let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder
    private let localSecret: String?

    public init(baseURL: URL, session: URLSession = .shared, localSecret: String? = nil) {
        self.baseURL = baseURL
        self.session = session
        self.localSecret = localSecret
        self.decoder = JSONDecoder()
        self.decoder.dateDecodingStrategy = .iso8601
        self.encoder = JSONEncoder()
        self.encoder.dateEncodingStrategy = .iso8601
    }

    public func registerDevice(name: String, platform: String, appVersion: String, bootstrapCode: String? = nil) async throws -> DeviceRegistration {
        let endpoint = baseURL.appending(path: "/api/v1/devices/register")
        let payload = RegisterDeviceRequest(deviceName: name, platform: platform, appVersion: appVersion, bootstrapCode: bootstrapCode)
        let response: DataEnvelope<DeviceRegistrationDTO> = try await send(endpoint: endpoint, method: "POST", body: payload)
        return response.data.toDomain()
    }

    public func previewJob(url: String, jobType: JobType, token: String) async throws -> JobPreview {
        let endpoint = baseURL
            .appending(path: "/api/v1/jobs")
            .appending(component: "preview")
        let payload = PreviewJobRequest(url: url, jobType: jobType)
        let response: DataEnvelope<JobPreviewDTO> = try await send(endpoint: endpoint, method: "POST", body: payload, bearerToken: token)
        return response.data.toDomain()
    }

    public func createJob(url: String, preferredQuality: String?, token: String) async throws -> Job {
        let endpoint = baseURL.appending(path: "/api/v1/jobs")
        let payload = CreateJobRequest(url: url, preferredQuality: preferredQuality)
        let response: DataEnvelope<JobDTO> = try await send(endpoint: endpoint, method: "POST", body: payload, bearerToken: token)
        return response.data.toDomain()
    }

    public func createAudioDownloadJob(url: String, token: String) async throws -> Job {
        let endpoint = baseURL.appending(path: "/api/v1/jobs/audio-download")
        let payload = CreateJobRequest(url: url, preferredQuality: nil)
        let response: DataEnvelope<JobDTO> = try await send(endpoint: endpoint, method: "POST", body: payload, bearerToken: token)
        return response.data.toDomain()
    }

    public func createAudioSeparationJob(fileURL: URL, token: String) async throws -> Job {
        let endpoint = baseURL.appending(path: "/api/v1/jobs/audio-separation")
        try validateAuthenticatedTransport(endpoint: endpoint, bearerToken: token)
        let boundary = "Boundary-\(UUID().uuidString)"
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        addLocalSecretHeader(to: &request)
        request.httpBody = try await Task.detached(priority: .utility) {
            try Self.multipartBody(fileURL: fileURL, boundary: boundary)
        }.value
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        if (200..<300).contains(httpResponse.statusCode) {
            let envelope = try decoder.decode(DataEnvelope<JobDTO>.self, from: data)
            return envelope.data.toDomain()
        }
        if let errorEnvelope = try? decoder.decode(ErrorEnvelope.self, from: data) {
            throw APIClientError.server(errorEnvelope.error.userMessage)
        }
        throw APIClientError.server("请求失败。")
    }

    public func youtubeCookieStatus(token: String) async throws -> YouTubeCookieStatus {
        let endpoint = baseURL
            .appending(path: "/api/v1/youtube/cookies/status")
        let response: DataEnvelope<YouTubeCookieStatusDTO> = try await sendWithoutBody(endpoint: endpoint, method: "GET", bearerToken: token)
        return response.data.toDomain()
    }

    public func uploadYouTubeCookies(fileURL: URL, token: String) async throws -> YouTubeCookieStatus {
        try await uploadCookieFile(fileURL: fileURL, token: token, path: "/api/v1/youtube/cookies")
    }

    public func deleteYouTubeCookies(token: String) async throws -> YouTubeCookieStatus {
        let endpoint = baseURL.appending(path: "/api/v1/youtube/cookies")
        let response: DataEnvelope<YouTubeCookieStatusDTO> = try await sendWithoutBody(endpoint: endpoint, method: "DELETE", bearerToken: token)
        return response.data.toDomain()
    }

    public func cancelJob(id: String, token: String) async throws -> Job {
        let endpoint = baseURL
            .appending(path: "/api/v1/jobs")
            .appending(component: id)
            .appending(component: "cancel")
        let response: DataEnvelope<JobDTO> = try await sendWithoutBody(endpoint: endpoint, method: "POST", bearerToken: token)
        return response.data.toDomain()
    }

    public func retryJob(id: String, token: String) async throws -> Job {
        let endpoint = baseURL
            .appending(path: "/api/v1/jobs")
            .appending(component: id)
            .appending(component: "retry")
        let response: DataEnvelope<JobDTO> = try await sendWithoutBody(endpoint: endpoint, method: "POST", bearerToken: token)
        return response.data.toDomain()
    }

    public func pauseJob(id: String, token: String) async throws -> Job {
        let endpoint = baseURL
            .appending(path: "/api/v1/jobs")
            .appending(component: id)
            .appending(component: "pause")
        let response: DataEnvelope<JobDTO> = try await sendWithoutBody(endpoint: endpoint, method: "POST", bearerToken: token)
        return response.data.toDomain()
    }

    public func resumeJob(id: String, token: String) async throws -> Job {
        let endpoint = baseURL
            .appending(path: "/api/v1/jobs")
            .appending(component: id)
            .appending(component: "resume")
        let response: DataEnvelope<JobDTO> = try await sendWithoutBody(endpoint: endpoint, method: "POST", bearerToken: token)
        return response.data.toDomain()
    }

    public func setJobPriority(id: String, priority: Int, token: String) async throws -> Job {
        let endpoint = baseURL
            .appending(path: "/api/v1/jobs")
            .appending(component: id)
            .appending(component: "priority")
        let payload = UpdateJobPriorityRequest(priority: priority)
        let response: DataEnvelope<JobDTO> = try await send(endpoint: endpoint, method: "POST", body: payload, bearerToken: token)
        return response.data.toDomain()
    }

    public func batchRetryJobs(token: String) async throws -> [Job] {
        let endpoint = baseURL
            .appending(path: "/api/v1/jobs")
            .appending(component: "batch-retry")
        let response: DataEnvelope<JobsListDTO> = try await sendWithoutBody(endpoint: endpoint, method: "POST", bearerToken: token)
        return response.data.items.map { $0.toDomain() }
    }

    public func listJobs(token: String) async throws -> [Job] {
        let endpoint = baseURL.appending(path: "/api/v1/jobs")
        let response: DataEnvelope<JobsListDTO> = try await sendWithoutBody(endpoint: endpoint, method: "GET", bearerToken: token)
        return response.data.items.map { $0.toDomain() }
    }

    public func listJobArtifacts(jobID: String, token: String) async throws -> [ArtifactSummary] {
        let endpoint = baseURL
            .appending(path: "/api/v1/jobs")
            .appending(component: jobID)
            .appending(component: "artifacts")
        let response: DataEnvelope<JobArtifactsDTO> = try await sendWithoutBody(endpoint: endpoint, method: "GET", bearerToken: token)
        return response.data.items.map { $0.toDomain() }
    }

    public func listJobLogs(jobID: String, token: String, limit: Int = 200, afterID: Int? = nil) async throws -> JobLogsResult {
        var endpoint = baseURL
            .appending(path: "/api/v1/jobs")
            .appending(component: jobID)
            .appending(component: "logs")
            .appending(queryItems: [URLQueryItem(name: "limit", value: String(limit))])
        if let afterID {
            endpoint = endpoint.appending(queryItems: [URLQueryItem(name: "after_id", value: String(afterID))])
        }
        let response: DataEnvelope<JobLogsDTO> = try await sendWithoutBody(endpoint: endpoint, method: "GET", bearerToken: token)
        return response.data.toDomain()
    }

    public func deleteArtifact(id: String, token: String) async throws {
        let endpoint = baseURL
            .appending(path: "/api/v1/artifacts")
            .appending(component: id)
        let _: DataEnvelope<DeleteArtifactDTO> = try await sendWithoutBody(endpoint: endpoint, method: "DELETE", bearerToken: token)
    }

    public func deleteHistory(token: String) async throws -> DeleteHistoryResult {
        let endpoint = baseURL
            .appending(path: "/api/v1/jobs")
            .appending(component: "history")
        let response: DataEnvelope<DeleteHistoryDTO> = try await sendWithoutBody(endpoint: endpoint, method: "DELETE", bearerToken: token)
        return response.data.toDomain()
    }

    public func deleteJob(id: String, token: String) async throws -> Job {
        let endpoint = baseURL
            .appending(path: "/api/v1/jobs")
            .appending(component: id)
        let response: DataEnvelope<JobDTO> = try await sendWithoutBody(endpoint: endpoint, method: "DELETE", bearerToken: token)
        return response.data.toDomain()
    }

    public func downloadArtifact(id: String, token: String) async throws -> DownloadedArtifact {
        let (temporaryURL, response) = try await session.download(for: try artifactDownloadRequest(id: id, token: token))
        return try decodeDownloadedArtifact(id: id, temporaryURL: temporaryURL, response: response)
    }

    public func downloadArtifact(
        id: String,
        token: String,
        onProgress: @Sendable @escaping (ArtifactDownloadProgress) async -> Void
    ) async throws -> DownloadedArtifact {
        let runner = ArtifactDownloadRunner(
            configuration: session.configuration,
            request: try artifactDownloadRequest(id: id, token: token),
            onProgress: onProgress
        )
        let (temporaryURL, response) = try await runner.download()
        return try decodeDownloadedArtifact(id: id, temporaryURL: temporaryURL, response: response)
    }

    private func artifactDownloadRequest(id: String, token: String) throws -> URLRequest {
        let endpoint = baseURL
            .appending(path: "/api/v1/artifacts")
            .appending(component: id)
            .appending(component: "download")
        try validateAuthenticatedTransport(endpoint: endpoint, bearerToken: token)
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        addLocalSecretHeader(to: &request)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return request
    }

    private func decodeDownloadedArtifact(id: String, temporaryURL: URL, response: URLResponse) throws -> DownloadedArtifact {
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        if (200..<300).contains(httpResponse.statusCode) {
            let fileName = Self.fileName(from: httpResponse.value(forHTTPHeaderField: "Content-Disposition")) ?? "\(id).mp4"
            return DownloadedArtifact(
                temporaryURL: temporaryURL,
                fileName: fileName,
                mimeType: httpResponse.value(forHTTPHeaderField: "Content-Type")
            )
        }
        let data = (try? Data(contentsOf: temporaryURL)) ?? Data()
        try? FileManager.default.removeItem(at: temporaryURL)
        if let errorEnvelope = try? decoder.decode(ErrorEnvelope.self, from: data) {
            throw APIClientError.server(errorEnvelope.error.userMessage)
        }
        throw APIClientError.server("请求失败。")
    }

    private func uploadCookieFile(fileURL: URL, token: String, path: String) async throws -> YouTubeCookieStatus {
        guard Self.isSecureUploadBaseURL(baseURL) else {
            throw APIClientError.server("为保护登录 Cookie，云端上传必须使用 HTTPS。")
        }
        let endpoint = baseURL.appending(path: path)
        let boundary = "Boundary-\(UUID().uuidString)"
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        addLocalSecretHeader(to: &request)
        request.httpBody = try await Task.detached(priority: .utility) {
            try Self.multipartBody(fileURL: fileURL, boundary: boundary)
        }.value
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        if (200..<300).contains(httpResponse.statusCode) {
            let envelope = try decoder.decode(DataEnvelope<YouTubeCookieStatusDTO>.self, from: data)
            return envelope.data.toDomain()
        }
        if let errorEnvelope = try? decoder.decode(ErrorEnvelope.self, from: data) {
            throw APIClientError.server(errorEnvelope.error.userMessage)
        }
        throw APIClientError.server("请求失败。")
    }

    private static func multipartBody(fileURL: URL, boundary: String) throws -> Data {
        var body = Data()
        let fileName = safeMultipartFileName(fileURL.lastPathComponent.isEmpty ? "audio" : fileURL.lastPathComponent)
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(fileName)\"\r\n")
        body.append("Content-Type: \(mimeType(for: fileURL))\r\n\r\n")
        body.append(try Data(contentsOf: fileURL))
        body.append("\r\n--\(boundary)--\r\n")
        return body
    }

    private static func safeMultipartFileName(_ value: String) -> String {
        value
            .replacingOccurrences(of: "\\", with: "_")
            .replacingOccurrences(of: "\r", with: " ")
            .replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "\"", with: "_")
    }

    private static func mimeType(for fileURL: URL) -> String {
        switch fileURL.pathExtension.lowercased() {
        case "txt": "text/plain"
        case "mp3": "audio/mpeg"
        case "wav": "audio/wav"
        case "m4a": "audio/mp4"
        case "aac": "audio/aac"
        case "flac": "audio/flac"
        default: "application/octet-stream"
        }
    }

    private static func fileName(from contentDisposition: String?) -> String? {
        guard let contentDisposition else { return nil }
        let parts = contentDisposition.split(separator: ";")
        for part in parts {
            let trimmed = part.trimmingCharacters(in: .whitespaces)
            if trimmed.lowercased().hasPrefix("filename*=") {
                let value = trimmed.dropFirst("filename*=".count).trimmingCharacters(in: CharacterSet(charactersIn: "\""))
                if let encodedName = value.split(separator: "'", maxSplits: 2).last,
                   let decodedName = String(encodedName).removingPercentEncoding,
                   !decodedName.isEmpty {
                    return decodedName
                }
            }
            if trimmed.lowercased().hasPrefix("filename=") {
                return trimmed.dropFirst("filename=".count).trimmingCharacters(in: CharacterSet(charactersIn: "\""))
            }
        }
        return nil
    }

    private func addLocalSecretHeader(to request: inout URLRequest) {
        guard let localSecret, Self.isLoopbackHost(request.url?.host(percentEncoded: false)) else { return }
        request.setValue(localSecret, forHTTPHeaderField: "X-XDownloader-Local-Secret")
    }

    private func validateAuthenticatedTransport(endpoint: URL, bearerToken: String?) throws {
        guard bearerToken != nil else { return }
        guard endpoint.scheme == "https" || Self.isLoopbackHost(endpoint.host(percentEncoded: false)) else {
            throw APIClientError.server("远程服务器必须使用 HTTPS。")
        }
    }

    private static func isLoopbackHost(_ host: String?) -> Bool {
        guard let host = host?.lowercased() else { return false }
        return host == "127.0.0.1" || host == "localhost" || host == "::1" || host == "[::1]"
    }

    private static func isSecureUploadBaseURL(_ url: URL) -> Bool {
        url.scheme == "https" || isLoopbackHost(url.host(percentEncoded: false))
    }

    private func send<Response: Decodable, Body: Encodable>(endpoint: URL, method: String, body: Body? = nil, bearerToken: String? = nil) async throws -> Response {
        try validateAuthenticatedTransport(endpoint: endpoint, bearerToken: bearerToken)
        var request = URLRequest(url: endpoint)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addLocalSecretHeader(to: &request)
        if let bearerToken {
            request.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.httpBody = try encoder.encode(body)
        }
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        if (200..<300).contains(httpResponse.statusCode) {
            return try decoder.decode(Response.self, from: data)
        }
        if let errorEnvelope = try? decoder.decode(ErrorEnvelope.self, from: data) {
            throw APIClientError.server(errorEnvelope.error.userMessage)
        }
        throw APIClientError.server("请求失败。")
    }

    private func sendWithoutBody<Response: Decodable>(endpoint: URL, method: String, bearerToken: String? = nil) async throws -> Response {
        try validateAuthenticatedTransport(endpoint: endpoint, bearerToken: bearerToken)
        var request = URLRequest(url: endpoint)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addLocalSecretHeader(to: &request)
        if let bearerToken {
            request.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        if (200..<300).contains(httpResponse.statusCode) {
            return try decoder.decode(Response.self, from: data)
        }
        if let errorEnvelope = try? decoder.decode(ErrorEnvelope.self, from: data) {
            throw APIClientError.server(errorEnvelope.error.userMessage)
        }
        throw APIClientError.server("请求失败。")
    }
}

private final class ArtifactDownloadRunner: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private let configuration: URLSessionConfiguration
    private let request: URLRequest
    private let onProgress: @Sendable (ArtifactDownloadProgress) async -> Void
    private let lock = NSLock()
    private var continuation: CheckedContinuation<(URL, URLResponse), Error>?
    private var session: URLSession?
    private var startTime = Date()

    init(
        configuration: URLSessionConfiguration,
        request: URLRequest,
        onProgress: @Sendable @escaping (ArtifactDownloadProgress) async -> Void
    ) {
        self.configuration = configuration
        self.request = request
        self.onProgress = onProgress
    }

    func download() async throws -> (URL, URLResponse) {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
                lock.withLock {
                    self.continuation = continuation
                    self.startTime = Date()
                    self.session = session
                }
                session.downloadTask(with: request).resume()
            }
        } onCancel: {
            lock.withLock { session }?.invalidateAndCancel()
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        guard let response = downloadTask.response else {
            finish(session: session, result: .failure(APIClientError.invalidResponse))
            return
        }
        let temporaryURL = FileManager.default.temporaryDirectory.appending(path: "artifact-download-\(UUID().uuidString)")
        do {
            if FileManager.default.fileExists(atPath: temporaryURL.path) {
                try FileManager.default.removeItem(at: temporaryURL)
            }
            try FileManager.default.moveItem(at: location, to: temporaryURL)
            finish(session: session, result: .success((temporaryURL, response)))
        } catch {
            finish(session: session, result: .failure(error))
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error {
            finish(session: session, result: .failure(error))
        }
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didWriteData bytesWritten: Int64,
        totalBytesWritten: Int64,
        totalBytesExpectedToWrite: Int64
    ) {
        let totalBytes = totalBytesExpectedToWrite > 0 ? totalBytesExpectedToWrite : nil
        let elapsed = max(Date().timeIntervalSince(startTime), 0.001)
        let bytesPerSecond = Double(totalBytesWritten) / elapsed
        let remainingBytes = totalBytes.map { max($0 - totalBytesWritten, 0) }
        let progress = ArtifactDownloadProgress(
            receivedBytes: totalBytesWritten,
            totalBytes: totalBytes,
            fraction: totalBytes.map { min(max(Double(totalBytesWritten) / Double($0), 0), 1) },
            bytesPerSecond: bytesPerSecond,
            etaSeconds: remainingBytes.map { bytesPerSecond > 0 ? Double($0) / bytesPerSecond : 0 }
        )
        Task { await onProgress(progress) }
    }

    private func finish(session: URLSession, result: Result<(URL, URLResponse), Error>) {
        let continuation = lock.withLock {
            let continuation = self.continuation
            self.continuation = nil
            self.session = nil
            return continuation
        }
        guard let continuation else { return }
        switch result {
        case let .success(value):
            continuation.resume(returning: value)
        case let .failure(error):
            continuation.resume(throwing: error)
        }
        session.finishTasksAndInvalidate()
    }
}

private struct DataEnvelope<T: Decodable>: Decodable {
    let data: T
}

private struct ErrorEnvelope: Decodable {
    struct APIError: Decodable {
        let code: String
        let message: String
        let userMessage: String

        private enum CodingKeys: String, CodingKey {
            case code
            case message
            case userMessage = "user_message"
        }
    }

    let error: APIError
}

private struct RegisterDeviceRequest: Encodable {
    let deviceName: String
    let platform: String
    let appVersion: String
    let bootstrapCode: String?

    private enum CodingKeys: String, CodingKey {
        case deviceName = "device_name"
        case platform
        case appVersion = "app_version"
        case bootstrapCode = "bootstrap_code"
    }
}

private struct PreviewJobRequest: Encodable {
    let url: String
    let jobType: JobType

    private enum CodingKeys: String, CodingKey {
        case url
        case jobType = "job_type"
    }
}

private struct CreateJobRequest: Encodable {
    let url: String
    let preferredQuality: String?

    private enum CodingKeys: String, CodingKey {
        case url
        case preferredQuality = "preferred_quality"
    }
}

private struct UpdateJobPriorityRequest: Encodable {
    let priority: Int
}

private struct DeviceRegistrationDTO: Decodable {
    let deviceID: String
    let accessToken: String
    let tokenType: String

    private enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case accessToken = "access_token"
        case tokenType = "token_type"
    }

    func toDomain() -> DeviceRegistration {
        DeviceRegistration(deviceID: deviceID, accessToken: accessToken, tokenType: tokenType)
    }
}

private struct JobsListDTO: Decodable {
    let items: [JobDTO]
}

private struct JobArtifactsDTO: Decodable {
    let items: [ArtifactSummaryDTO]
}

private struct JobLogsDTO: Decodable {
    let jobID: String
    let items: [JobLogEventDTO]

    private enum CodingKeys: String, CodingKey {
        case jobID = "job_id"
        case items
    }

    func toDomain() -> JobLogsResult {
        JobLogsResult(jobID: jobID, items: items.map { $0.toDomain() })
    }
}

private struct JobLogEventDTO: Decodable {
    let id: Int
    let jobID: String
    let level: String
    let eventType: String
    let message: String
    let createdAt: Date

    private enum CodingKeys: String, CodingKey {
        case id
        case jobID = "job_id"
        case level
        case eventType = "event_type"
        case message
        case createdAt = "created_at"
    }

    func toDomain() -> JobLogEvent {
        JobLogEvent(id: id, jobID: jobID, level: level, eventType: eventType, message: message, createdAt: createdAt)
    }
}

private struct JobPreviewDTO: Decodable {
    let sourceURL: String
    let normalizedURL: String
    let provider: String
    let title: String?
    let authorHandle: String?
    let thumbnailURL: String?
    let fileExtension: String
    let recommendedJobType: JobType
    let existingJobID: String?
    let existingArtifactID: String?
    let existingFileName: String?
    let existingLocalPath: String?
    let canReuseExisting: Bool

    private enum CodingKeys: String, CodingKey {
        case sourceURL = "source_url"
        case normalizedURL = "normalized_url"
        case provider
        case title
        case authorHandle = "author_handle"
        case thumbnailURL = "thumbnail_url"
        case fileExtension = "file_extension"
        case recommendedJobType = "recommended_job_type"
        case existingJobID = "existing_job_id"
        case existingArtifactID = "existing_artifact_id"
        case existingFileName = "existing_file_name"
        case existingLocalPath = "existing_local_path"
        case canReuseExisting = "can_reuse_existing"
    }

    func toDomain() -> JobPreview {
        JobPreview(
            sourceURL: sourceURL,
            normalizedURL: normalizedURL,
            provider: provider,
            title: title,
            authorHandle: authorHandle,
            thumbnailURL: thumbnailURL,
            fileExtension: fileExtension,
            recommendedJobType: recommendedJobType,
            existingJobID: existingJobID,
            existingArtifactID: existingArtifactID,
            existingFileName: existingFileName,
            existingLocalPath: existingLocalPath,
            canReuseExisting: canReuseExisting
        )
    }
}

private struct DeleteArtifactDTO: Decodable {
    let deleted: Bool
}

private struct YouTubeCookieStatusDTO: Decodable {
    let isConfigured: Bool
    let fileSize: Int?
    let updatedAt: Date?

    private enum CodingKeys: String, CodingKey {
        case isConfigured = "is_configured"
        case fileSize = "file_size"
        case updatedAt = "updated_at"
    }

    func toDomain() -> YouTubeCookieStatus {
        YouTubeCookieStatus(isConfigured: isConfigured, fileSize: fileSize, updatedAt: updatedAt)
    }
}

private struct DeleteHistoryDTO: Decodable {
    let deletedCount: Int
    let skippedActiveCount: Int
    let deletedJobIDs: [String]

    private enum CodingKeys: String, CodingKey {
        case deletedCount = "deleted_count"
        case skippedActiveCount = "skipped_active_count"
        case deletedJobIDs = "deleted_job_ids"
    }

    func toDomain() -> DeleteHistoryResult {
        DeleteHistoryResult(deletedCount: deletedCount, skippedActiveCount: skippedActiveCount, deletedJobIDs: deletedJobIDs)
    }
}

private struct ArtifactSummaryDTO: Decodable {
    let id: String
    let jobID: String
    let fileName: String
    let mimeType: String
    let role: ArtifactRole
    let fileSize: Int
    let localPath: String?
    let thumbnailLocalPath: String?
    let durationSeconds: Double?
    let width: Int?
    let height: Int?
    let videoCodec: String?
    let audioCodec: String?
    let bitrateKbps: Int?
    let containerFormat: String?
    let createdAt: Date

    private enum CodingKeys: String, CodingKey {
        case id
        case jobID = "job_id"
        case fileName = "file_name"
        case mimeType = "mime_type"
        case role
        case fileSize = "file_size"
        case localPath = "local_path"
        case thumbnailLocalPath = "thumbnail_local_path"
        case durationSeconds = "duration_seconds"
        case width
        case height
        case videoCodec = "video_codec"
        case audioCodec = "audio_codec"
        case bitrateKbps = "bitrate_kbps"
        case containerFormat = "container_format"
        case createdAt = "created_at"
    }

    func toDomain() -> ArtifactSummary {
        ArtifactSummary(
            id: id,
            jobID: jobID,
            fileName: fileName,
            mimeType: mimeType,
            role: role,
            fileSize: fileSize,
            localPath: localPath,
            thumbnailLocalPath: thumbnailLocalPath,
            durationSeconds: durationSeconds,
            width: width,
            height: height,
            videoCodec: videoCodec,
            audioCodec: audioCodec,
            bitrateKbps: bitrateKbps,
            containerFormat: containerFormat,
            createdAt: createdAt
        )
    }
}

private struct JobDTO: Decodable {
    let id: String
    let deviceID: String
    let sourceURL: String
    let normalizedURL: String
    let provider: String?
    let jobType: JobType
    let status: JobStatus
    let progress: Int
    let priority: Int
    let downloadedBytes: Int?
    let totalBytes: Int?
    let speedBytesPerSec: Int?
    let etaSeconds: Int?
    let errorCode: String?
    let errorMessage: String?
    let userMessage: String?
    let mediaTitle: String?
    let authorHandle: String?
    let thumbnailURL: String?
    let artifactID: String?
    let selectedQuality: String?
    let createdAt: Date
    let updatedAt: Date
    let finishedAt: Date?

    private enum CodingKeys: String, CodingKey {
        case id
        case deviceID = "device_id"
        case sourceURL = "source_url"
        case normalizedURL = "normalized_url"
        case provider
        case jobType = "job_type"
        case status
        case progress
        case priority
        case downloadedBytes = "downloaded_bytes"
        case totalBytes = "total_bytes"
        case speedBytesPerSec = "speed_bytes_per_sec"
        case etaSeconds = "eta_seconds"
        case errorCode = "error_code"
        case errorMessage = "error_message"
        case userMessage = "user_message"
        case mediaTitle = "media_title"
        case authorHandle = "author_handle"
        case thumbnailURL = "thumbnail_url"
        case artifactID = "artifact_id"
        case selectedQuality = "selected_quality"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case finishedAt = "finished_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.id = try container.decode(String.self, forKey: .id)
        self.deviceID = try container.decode(String.self, forKey: .deviceID)
        self.sourceURL = try container.decode(String.self, forKey: .sourceURL)
        self.normalizedURL = try container.decode(String.self, forKey: .normalizedURL)
        self.provider = try container.decodeIfPresent(String.self, forKey: .provider)
        self.jobType = try container.decodeIfPresent(JobType.self, forKey: .jobType) ?? .download
        self.status = try container.decode(JobStatus.self, forKey: .status)
        self.progress = try container.decode(Int.self, forKey: .progress)
        self.priority = try container.decodeIfPresent(Int.self, forKey: .priority) ?? 0
        self.downloadedBytes = try container.decodeIfPresent(Int.self, forKey: .downloadedBytes)
        self.totalBytes = try container.decodeIfPresent(Int.self, forKey: .totalBytes)
        self.speedBytesPerSec = try container.decodeIfPresent(Int.self, forKey: .speedBytesPerSec)
        self.etaSeconds = try container.decodeIfPresent(Int.self, forKey: .etaSeconds)
        self.errorCode = try container.decodeIfPresent(String.self, forKey: .errorCode)
        self.errorMessage = try container.decodeIfPresent(String.self, forKey: .errorMessage)
        self.userMessage = try container.decodeIfPresent(String.self, forKey: .userMessage)
        self.mediaTitle = try container.decodeIfPresent(String.self, forKey: .mediaTitle)
        self.authorHandle = try container.decodeIfPresent(String.self, forKey: .authorHandle)
        self.thumbnailURL = try container.decodeIfPresent(String.self, forKey: .thumbnailURL)
        self.artifactID = try container.decodeIfPresent(String.self, forKey: .artifactID)
        self.selectedQuality = try container.decodeIfPresent(String.self, forKey: .selectedQuality)
        self.createdAt = try container.decode(Date.self, forKey: .createdAt)
        self.updatedAt = try container.decode(Date.self, forKey: .updatedAt)
        self.finishedAt = try container.decodeIfPresent(Date.self, forKey: .finishedAt)
    }

    func toDomain() -> Job {
        Job(
            id: id,
            deviceID: deviceID,
            sourceURL: sourceURL,
            normalizedURL: normalizedURL,
            provider: provider,
            jobType: jobType,
            status: status,
            progress: progress,
            priority: priority,
            downloadedBytes: downloadedBytes,
            totalBytes: totalBytes,
            speedBytesPerSec: speedBytesPerSec,
            etaSeconds: etaSeconds,
            errorCode: errorCode,
            errorMessage: errorMessage,
            userMessage: userMessage,
            mediaTitle: mediaTitle,
            authorHandle: authorHandle,
            thumbnailURL: thumbnailURL,
            artifactID: artifactID,
            selectedQuality: selectedQuality,
            createdAt: createdAt,
            updatedAt: updatedAt,
            finishedAt: finishedAt
        )
    }
}
