import AppCore
import Foundation
import Networking
import Testing

private final class URLProtocolStub: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private func makeSession() -> URLSession {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [URLProtocolStub.self]
    return URLSession(configuration: configuration)
}

private func requestBodyString(_ request: URLRequest) -> String {
    if let httpBody = request.httpBody {
        return String(data: httpBody, encoding: .utf8) ?? ""
    }
    guard let stream = request.httpBodyStream else {
        return ""
    }
    stream.open()
    defer { stream.close() }
    var data = Data()
    var buffer = [UInt8](repeating: 0, count: 4096)
    while stream.hasBytesAvailable {
        let count = stream.read(&buffer, maxLength: buffer.count)
        if count <= 0 {
            break
        }
        data.append(buffer, count: count)
    }
    return String(data: data, encoding: .utf8) ?? ""
}

private let jobResponse = Data(#"""
{
  "data": {
    "id": "job-1",
    "device_id": "device-1",
    "source_url": "upload:song.mp3",
    "normalized_url": "file:/tmp/song.mp3",
    "provider": null,
	    "job_type": "audio_separation",
	    "status": "queued",
	    "progress": 0,
	    "priority": 0,
	    "downloaded_bytes": null,
    "total_bytes": null,
    "speed_bytes_per_sec": null,
    "eta_seconds": null,
    "error_code": null,
    "error_message": null,
    "user_message": null,
    "media_title": "song",
    "author_handle": null,
    "thumbnail_url": null,
    "artifact_id": null,
    "selected_quality": null,
    "created_at": "2026-04-27T00:00:00Z",
    "updated_at": "2026-04-27T00:00:00Z",
    "finished_at": null
  }
}
"""#.utf8)

@Suite(.serialized)
struct APIClientTests {
@Test func apiClientRegistersDeviceWithBootstrapCode() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://124.221.197.94:18767")!, session: makeSession())
    let response = Data(#"{"data":{"device_id":"device-1","access_token":"token-1","token_type":"bearer"}}"#.utf8)

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/devices/register")
        #expect(request.httpMethod == "POST")
        let body = requestBodyString(request)
        #expect(body.contains("\"bootstrap_code\":\"cloud-code\""))
        #expect(body.contains("\"platform\":\"ios\""))
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, response)
    }

    let registration = try await apiClient.registerDevice(name: "iPhone", platform: "ios", appVersion: "0.1.0", bootstrapCode: "cloud-code")

    #expect(registration.deviceID == "device-1")
    #expect(registration.accessToken == "token-1")
}

@Test func apiClientCancelsJob() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/jobs/job-1/cancel")
        #expect(request.httpMethod == "POST")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
        #expect(requestBodyString(request).isEmpty)
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, jobResponse)
    }

    let job = try await apiClient.cancelJob(id: "job-1", token: "token-1")

    #expect(job.id == "job-1")
}

@Test func apiClientRetriesJob() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/jobs/job-1/retry")
        #expect(request.httpMethod == "POST")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
        #expect(requestBodyString(request).isEmpty)
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, jobResponse)
    }

    let job = try await apiClient.retryJob(id: "job-1", token: "token-1")

	    #expect(job.id == "job-1")
	}

    @Test func apiClientPausesAndResumesJob() async throws {
        let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")
        var requestedPaths: [String] = []

        URLProtocolStub.handler = { request in
            requestedPaths.append(request.url?.path ?? "")
            #expect(request.httpMethod == "POST")
            #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
            #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
            #expect(requestBodyString(request).isEmpty)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, jobResponse)
        }

        _ = try await apiClient.pauseJob(id: "job-1", token: "token-1")
        _ = try await apiClient.resumeJob(id: "job-1", token: "token-1")

        #expect(requestedPaths == ["/api/v1/jobs/job-1/pause", "/api/v1/jobs/job-1/resume"])
    }

    @Test func apiClientSetsJobPriority() async throws {
        let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")

        URLProtocolStub.handler = { request in
            #expect(request.url?.path == "/api/v1/jobs/job-1/priority")
            #expect(request.httpMethod == "POST")
            #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
            #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
            #expect(requestBodyString(request).contains("\"priority\":30"))
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, jobResponse)
        }

        let job = try await apiClient.setJobPriority(id: "job-1", priority: 30, token: "token-1")

        #expect(job.id == "job-1")
    }

    @Test func apiClientBatchRetriesJobs() async throws {
        let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")
        let response = Data(#"""
        {
          "data": {
            "items": [
              {
                "id": "job-1",
                "device_id": "device-1",
                "source_url": "https://x.com/demo/status/1",
                "normalized_url": "https://x.com/demo/status/1",
                "provider": null,
                "job_type": "download",
                "status": "queued",
                "progress": 0,
                "priority": 0,
                "downloaded_bytes": null,
                "total_bytes": null,
                "speed_bytes_per_sec": null,
                "eta_seconds": null,
                "error_code": null,
                "error_message": null,
                "user_message": null,
                "media_title": null,
                "author_handle": null,
                "thumbnail_url": null,
                "artifact_id": null,
                "selected_quality": null,
                "created_at": "2026-04-27T00:00:00Z",
                "updated_at": "2026-04-27T00:00:00Z",
                "finished_at": null
              }
            ]
          }
        }
        """#.utf8)

        URLProtocolStub.handler = { request in
            #expect(request.url?.path == "/api/v1/jobs/batch-retry")
            #expect(request.httpMethod == "POST")
            #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
            #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
            #expect(requestBodyString(request).isEmpty)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, response)
        }

        let jobs = try await apiClient.batchRetryJobs(token: "token-1")

        #expect(jobs.map(\.id) == ["job-1"])
    }

	@Test func apiClientPreviewsJob() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")
    let response = Data(#"""
    {
      "data": {
        "source_url": "https://x.com/demo/status/12345?foo=bar",
        "normalized_url": "https://x.com/demo/status/12345",
        "provider": "yt-dlp",
        "title": "Preview title",
        "author_handle": "author",
        "thumbnail_url": "https://example.com/thumb.jpg",
        "file_extension": "mp4",
        "recommended_job_type": "download",
        "existing_job_id": "job-1",
        "existing_artifact_id": "artifact-1",
        "existing_file_name": "existing.mp4",
        "existing_local_path": "/tmp/existing.mp4",
        "can_reuse_existing": true
      }
    }
    """#.utf8)

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/jobs/preview")
        #expect(request.httpMethod == "POST")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
        let body = requestBodyString(request)
        #expect(body.contains("12345"))
        #expect(body.contains("\"job_type\":\"download\""))
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, response)
    }

    let preview = try await apiClient.previewJob(url: "https://x.com/demo/status/12345?foo=bar", jobType: .download, token: "token-1")

    #expect(preview.sourceURL == "https://x.com/demo/status/12345?foo=bar")
    #expect(preview.normalizedURL == "https://x.com/demo/status/12345")
    #expect(preview.provider == "yt-dlp")
    #expect(preview.title == "Preview title")
    #expect(preview.recommendedJobType == .download)
    #expect(preview.canReuseExisting)
    #expect(preview.existingJobID == "job-1")
    #expect(preview.existingArtifactID == "artifact-1")
    #expect(preview.existingFileName == "existing.mp4")
    #expect(preview.existingLocalPath == "/tmp/existing.mp4")
}

@Test func apiClientListsJobLogs() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")
    let response = Data(#"""
    {
      "data": {
        "job_id": "job-1",
        "items": [
          {
            "id": 1,
            "job_id": "job-1",
            "level": "info",
            "event_type": "resolving",
            "message": "开始解析链接",
            "created_at": "2026-05-08T00:00:00Z"
          }
        ]
      }
    }
    """#.utf8)

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/jobs/job-1/logs")
        #expect(request.url?.query == "limit=50&after_id=10")
        #expect(request.httpMethod == "GET")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, response)
    }

    let logs = try await apiClient.listJobLogs(jobID: "job-1", token: "token-1", limit: 50, afterID: 10)

    #expect(logs.jobID == "job-1")
    #expect(logs.items.count == 1)
    #expect(logs.items[0].eventType == "resolving")
    #expect(logs.items[0].message == "开始解析链接")
}

@Test func apiClientRejectsAuthenticatedRemoteHTTPRequests() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://example.com")!, session: makeSession())

    await #expect(throws: APIClientError.self) {
        _ = try await apiClient.listJobLogs(jobID: "job-1", token: "token-1", limit: 50, afterID: nil)
    }
}

@Test func apiClientRejectsRemoteHTTPAudioSeparationUpload() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://example.com")!, session: makeSession())
    let fileURL = FileManager.default.temporaryDirectory.appending(path: "audio-\(UUID().uuidString).mp3")
    try Data("audio".utf8).write(to: fileURL)
    defer { try? FileManager.default.removeItem(at: fileURL) }

    await #expect(throws: APIClientError.self) {
        _ = try await apiClient.createAudioSeparationJob(fileURL: fileURL, token: "token-1")
    }
}

@Test func apiClientRejectsRemoteHTTPArtifactDownload() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://example.com")!, session: makeSession())

    await #expect(throws: APIClientError.self) {
        _ = try await apiClient.downloadArtifact(id: "artifact-1", token: "token-1")
    }
}

@Test func apiClientCreatesAudioDownloadJob() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")
    let response = Data(#"""
    {
      "data": {
        "id": "job-1",
        "device_id": "device-1",
        "source_url": "https://www.youtube.com/watch?v=GEFehFHg_os",
        "normalized_url": "https://www.youtube.com/watch?v=GEFehFHg_os",
        "provider": null,
        "job_type": "audio_download",
        "status": "queued",
        "progress": 0,
        "downloaded_bytes": null,
        "total_bytes": null,
        "speed_bytes_per_sec": null,
        "eta_seconds": null,
        "error_code": null,
        "error_message": null,
        "user_message": null,
        "media_title": null,
        "author_handle": null,
        "thumbnail_url": null,
        "artifact_id": null,
        "selected_quality": null,
        "created_at": "2026-04-27T00:00:00Z",
        "updated_at": "2026-04-27T00:00:00Z",
        "finished_at": null
      }
    }
    """#.utf8)

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/jobs/audio-download")
        #expect(request.httpMethod == "POST")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
        #expect(requestBodyString(request).contains("GEFehFHg_os"))
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, response)
    }

    let job = try await apiClient.createAudioDownloadJob(url: "https://www.youtube.com/watch?v=GEFehFHg_os", token: "token-1")

    #expect(job.id == "job-1")
    #expect(job.jobType == .audioDownload)
}

@Test func apiClientFetchesYouTubeCookieStatus() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")
    let response = Data(#"{"data":{"is_configured":true,"file_size":128,"updated_at":"2026-05-04T00:00:00Z"}}"#.utf8)

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/youtube/cookies/status")
        #expect(request.httpMethod == "GET")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, response)
    }

    let status = try await apiClient.youtubeCookieStatus(token: "token-1")

    #expect(status.isConfigured)
    #expect(status.fileSize == 128)
    #expect(status.updatedAt != nil)
}

@Test func apiClientUploadsYouTubeCookieMultipartRequest() async throws {
    let fileURL = FileManager.default.temporaryDirectory.appending(path: "cookies-\(UUID().uuidString).txt")
    try Data(".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tsecret".utf8).write(to: fileURL)
    defer { try? FileManager.default.removeItem(at: fileURL) }
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")
    let response = Data(#"{"data":{"is_configured":true,"file_size":64,"updated_at":"2026-05-04T00:00:00Z"}}"#.utf8)

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/youtube/cookies")
        #expect(request.httpMethod == "POST")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
        let contentType = request.value(forHTTPHeaderField: "Content-Type") ?? ""
        #expect(contentType.hasPrefix("multipart/form-data; boundary="))
        let body = requestBodyString(request)
        #expect(body.contains("name=\"file\"; filename=\""))
        #expect(body.contains("cookies-"))
        #expect(body.contains("Content-Type: text/plain"))
        #expect(body.contains(".youtube.com"))
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, response)
    }

    let status = try await apiClient.uploadYouTubeCookies(fileURL: fileURL, token: "token-1")

    #expect(status.isConfigured)
}

@Test func apiClientRejectsInsecureRemoteYouTubeCookieUploadBeforeRequest() async throws {
    let fileURL = FileManager.default.temporaryDirectory.appending(path: "cookies-\(UUID().uuidString).txt")
    try Data(".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tsecret".utf8).write(to: fileURL)
    defer { try? FileManager.default.removeItem(at: fileURL) }
    let apiClient = APIClient(baseURL: URL(string: "http://124.221.197.94:18767")!, session: makeSession())
    var didSendRequest = false
    URLProtocolStub.handler = { request in
        didSendRequest = true
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, Data())
    }

    do {
        _ = try await apiClient.uploadYouTubeCookies(fileURL: fileURL, token: "token-1")
        Issue.record("Expected insecure cookie upload to fail")
    } catch let error as APIClientError {
        #expect(error.localizedDescription == "为保护登录 Cookie，云端上传必须使用 HTTPS。")
    }
    #expect(!didSendRequest)
}

@Test func apiClientDeletesYouTubeCookies() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")
    let response = Data(#"{"data":{"is_configured":false,"file_size":null,"updated_at":null}}"#.utf8)

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/youtube/cookies")
        #expect(request.httpMethod == "DELETE")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, response)
    }

    let status = try await apiClient.deleteYouTubeCookies(token: "token-1")

    #expect(!status.isConfigured)
}

@Test func apiClientUploadsAudioSeparationMultipartRequest() async throws {
    let fileURL = FileManager.default.temporaryDirectory.appending(path: "song-\(UUID().uuidString).mp3")
    try Data("audio-bytes".utf8).write(to: fileURL)
    defer { try? FileManager.default.removeItem(at: fileURL) }
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/jobs/audio-separation")
        #expect(request.httpMethod == "POST")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
        let contentType = request.value(forHTTPHeaderField: "Content-Type") ?? ""
        #expect(contentType.hasPrefix("multipart/form-data; boundary="))
        let body = requestBodyString(request)
        #expect(body.contains("name=\"file\"; filename=\"") && body.contains(".mp3\""))
        #expect(body.contains("Content-Type: audio/mpeg"))
        #expect(body.contains("audio-bytes"))
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, jobResponse)
    }

    let job = try await apiClient.createAudioSeparationJob(fileURL: fileURL, token: "token-1")

    #expect(job.id == "job-1")
    #expect(job.jobType == .audioSeparation)
}

@Test func apiClientEscapesMultipartFileName() async throws {
    let directory = FileManager.default.temporaryDirectory.appending(path: "multipart-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let fileURL = directory.appending(path: "bad\\\"\r\nname.mp3")
    try Data("audio-bytes".utf8).write(to: fileURL)
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")

    URLProtocolStub.handler = { request in
        let body = requestBodyString(request)
        #expect(body.contains("filename=\"bad__  name.mp3\""))
        #expect(!body.contains("filename=\"bad\"\r\nname.mp3\""))
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, jobResponse)
    }

    _ = try await apiClient.createAudioSeparationJob(fileURL: fileURL, token: "token-1")
}

@Test func apiClientDoesNotSendLocalSecretToRemoteHost() async throws {
    let apiClient = APIClient(baseURL: URL(string: "https://example.com")!, session: makeSession(), localSecret: "secret-1")

    URLProtocolStub.handler = { request in
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == nil)
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, Data(#"{"data":{"items":[]}}"#.utf8))
    }

    _ = try await apiClient.listJobs(token: "token-1")
}

@Test func apiClientDecodesJobWithoutJobTypeAsDownload() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")
    let response = Data(#"""
    {
      "data": {
        "items": [{
          "id": "job-1",
          "device_id": "device-1",
          "source_url": "https://x.com/demo/status/1",
          "normalized_url": "https://x.com/demo/status/1",
          "provider": null,
          "status": "queued",
          "progress": 0,
          "downloaded_bytes": null,
          "total_bytes": null,
          "speed_bytes_per_sec": null,
          "eta_seconds": null,
          "error_code": null,
          "error_message": null,
          "user_message": null,
          "media_title": null,
          "author_handle": null,
          "thumbnail_url": null,
          "artifact_id": null,
          "selected_quality": null,
          "created_at": "2026-04-27T00:00:00Z",
          "updated_at": "2026-04-27T00:00:00Z",
          "finished_at": null
        }]
      }
    }
    """#.utf8)

    URLProtocolStub.handler = { request in
        (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, response)
    }

    let jobs = try await apiClient.listJobs(token: "token-1")

    #expect(jobs.first?.jobType == .download)
}

@Test func apiClientListsJobArtifacts() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")
    let response = Data(#"""
    {
      "data": {
        "items": [
          {"id":"vocals-1","job_id":"job-1","file_name":"song.vocals.wav","mime_type":"audio/wav","role":"vocals","file_size":10,"local_path":"/Users/test/Downloads/XDownloader/song.vocals.wav","thumbnail_local_path":null,"created_at":"2026-04-27T00:00:00Z"},
          {"id":"video-1","job_id":"job-1","file_name":"video.mp4","mime_type":"video/mp4","role":"media","file_size":20,"local_path":"/Users/test/Downloads/XDownloader/video.mp4","thumbnail_local_path":"/Users/test/Downloads/XDownloader/video.thumbnail.jpg","duration_seconds":12.5,"width":1920,"height":1080,"video_codec":"h264","audio_codec":"aac","bitrate_kbps":4500,"container_format":"mov,mp4,m4a,3gp,3g2,mj2","created_at":"2026-04-27T00:00:00Z"}
        ]
      }
    }
    """#.utf8)

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/jobs/job-1/artifacts")
        #expect(request.httpMethod == "GET")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, response)
    }

    let artifacts = try await apiClient.listJobArtifacts(jobID: "job-1", token: "token-1")

    #expect(artifacts.map(\.role) == [.vocals, .media])
    #expect(artifacts[0].localPath == "/Users/test/Downloads/XDownloader/song.vocals.wav")
    #expect(artifacts[0].thumbnailLocalPath == nil)
    #expect(artifacts[0].durationSeconds == nil)
    #expect(artifacts[1].thumbnailLocalPath == "/Users/test/Downloads/XDownloader/video.thumbnail.jpg")
    #expect(artifacts[1].isPlayableVideo)
    #expect(artifacts[1].durationSeconds == 12.5)
    #expect(artifacts[1].width == 1920)
    #expect(artifacts[1].height == 1080)
    #expect(artifacts[1].videoCodec == "h264")
    #expect(artifacts[1].audioCodec == "aac")
    #expect(artifacts[1].bitrateKbps == 4500)
    #expect(artifacts[1].containerFormat == "mov,mp4,m4a,3gp,3g2,mj2")
}

@Test func apiClientDownloadsArtifact() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/artifacts/artifact-1/download")
        #expect(request.httpMethod == "GET")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
        #expect(requestBodyString(request).isEmpty)
        return (
            HTTPURLResponse(
                url: request.url!,
                statusCode: 200,
                httpVersion: nil,
                headerFields: [
                    "Content-Disposition": "attachment; filename*=UTF-8''demo%20video.mp4",
                    "Content-Type": "video/mp4",
                ]
            )!,
            Data("video-bytes".utf8)
        )
    }

    let artifact = try await apiClient.downloadArtifact(id: "artifact-1", token: "token-1")
    defer { try? FileManager.default.removeItem(at: artifact.temporaryURL) }

    #expect(artifact.fileName == "demo video.mp4")
    #expect(artifact.mimeType == "video/mp4")
    #expect(try String(contentsOf: artifact.temporaryURL, encoding: .utf8) == "video-bytes")
}

@Test func apiClientDownloadArtifactThrowsServerMessage() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")
    let response = Data(#"{"error":{"code":"artifact_missing","message":"missing","user_message":"文件不存在。"}}"#.utf8)

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/artifacts/artifact-1/download")
        return (HTTPURLResponse(url: request.url!, statusCode: 404, httpVersion: nil, headerFields: nil)!, response)
    }

    do {
        _ = try await apiClient.downloadArtifact(id: "artifact-1", token: "token-1")
        Issue.record("Expected downloadArtifact to throw")
    } catch {
        #expect(error.localizedDescription == "文件不存在。")
    }
}

@Test func apiClientDeletesArtifact() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/artifacts/artifact-1")
        #expect(request.httpMethod == "DELETE")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
        #expect(requestBodyString(request).isEmpty)
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, Data(#"{"data":{"deleted":true}}"#.utf8))
    }

    try await apiClient.deleteArtifact(id: "artifact-1", token: "token-1")
}

@Test func apiClientDeletesHistory() async throws {
    let apiClient = APIClient(baseURL: URL(string: "http://127.0.0.1:18767")!, session: makeSession(), localSecret: "secret-1")

    URLProtocolStub.handler = { request in
        #expect(request.url?.path == "/api/v1/jobs/history")
        #expect(request.httpMethod == "DELETE")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token-1")
        #expect(request.value(forHTTPHeaderField: "X-XDownloader-Local-Secret") == "secret-1")
        #expect(requestBodyString(request).isEmpty)
        return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, Data(#"{"data":{"deleted_count":2,"skipped_active_count":1,"deleted_job_ids":["job-1","job-2"]}}"#.utf8))
    }

    let result = try await apiClient.deleteHistory(token: "token-1")

    #expect(result.deletedCount == 2)
    #expect(result.skippedActiveCount == 1)
    #expect(result.deletedJobIDs == ["job-1", "job-2"])
}
}
