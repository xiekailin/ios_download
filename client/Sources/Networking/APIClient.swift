import AppCore
import Foundation

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
    public var baseURL: URL
    public var session: URLSession
    public var decoder: JSONDecoder
    public var encoder: JSONEncoder

    public init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
        self.decoder = JSONDecoder()
        self.decoder.dateDecodingStrategy = .iso8601
        self.encoder = JSONEncoder()
        self.encoder.dateEncodingStrategy = .iso8601
    }

    public func registerDevice(name: String, platform: String, appVersion: String) async throws -> DeviceRegistration {
        let endpoint = baseURL.appending(path: "/api/v1/devices/register")
        let payload = RegisterDeviceRequest(deviceName: name, platform: platform, appVersion: appVersion)
        let response: DataEnvelope<DeviceRegistrationDTO> = try await send(endpoint: endpoint, method: "POST", body: payload)
        return response.data.toDomain()
    }

    public func createJob(url: String, preferredQuality: String?, token: String) async throws -> Job {
        let endpoint = baseURL.appending(path: "/api/v1/jobs")
        let payload = CreateJobRequest(url: url, preferredQuality: preferredQuality)
        let response: DataEnvelope<JobDTO> = try await send(endpoint: endpoint, method: "POST", body: payload, bearerToken: token)
        return response.data.toDomain()
    }

    public func listJobs(token: String) async throws -> [Job] {
        let endpoint = baseURL.appending(path: "/api/v1/jobs")
        let response: DataEnvelope<JobsListDTO> = try await sendWithoutBody(endpoint: endpoint, method: "GET", bearerToken: token)
        return response.data.items.map { $0.toDomain() }
    }

    public func deleteJob(id: String, token: String) async throws -> Job {
        let endpoint = baseURL.appending(path: "/api/v1/jobs/\(id)")
        let response: DataEnvelope<JobDTO> = try await sendWithoutBody(endpoint: endpoint, method: "DELETE", bearerToken: token)
        return response.data.toDomain()
    }

    private func send<Response: Decodable, Body: Encodable>(endpoint: URL, method: String, body: Body? = nil, bearerToken: String? = nil) async throws -> Response {
        var request = URLRequest(url: endpoint)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
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
        var request = URLRequest(url: endpoint)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
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

    private enum CodingKeys: String, CodingKey {
        case deviceName = "device_name"
        case platform
        case appVersion = "app_version"
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

private struct JobDTO: Decodable {
    let id: String
    let deviceID: String
    let sourceURL: String
    let normalizedURL: String
    let provider: String?
    let status: JobStatus
    let progress: Int
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
        case status
        case progress
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

    func toDomain() -> Job {
        Job(
            id: id,
            deviceID: deviceID,
            sourceURL: sourceURL,
            normalizedURL: normalizedURL,
            provider: provider,
            status: status,
            progress: progress,
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
