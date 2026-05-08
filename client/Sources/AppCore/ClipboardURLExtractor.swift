import Foundation

public struct URLExtractionResult: Sendable, Equatable {
    public let urls: [String]
    public let exceededLimit: Bool

    public init(urls: [String], exceededLimit: Bool) {
        self.urls = urls
        self.exceededLimit = exceededLimit
    }
}

public enum ClipboardURLExtractor {
    private static let trailingDelimiters = CharacterSet(charactersIn: #".,;:!?)]}、。，；：！？）】》>"#)
    private static let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue)

    public static func firstSupportedURL(in text: String) -> String? {
        supportedURLs(in: text).first
    }

    public static func supportedURLs(in text: String) -> [String] {
        supportedURLs(in: text, maxURLs: nil).urls
    }

    public static func supportedURLs(in text: String, maxURLs: Int?) -> URLExtractionResult {
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        let matches = detector?.matches(in: text, options: [], range: range) ?? []
        var urls: [String] = []
        var seenURLs = Set<String>()
        for match in matches {
            guard let matchRange = Range(match.range, in: text) else { continue }
            let candidate = String(text[matchRange]).trimmingCharacters(in: trailingDelimiters)
            guard AppController.isValidSupportedSourceURL(candidate), let dedupeKey = dedupeKey(for: candidate), !seenURLs.contains(dedupeKey) else {
                continue
            }
            if let maxURLs, urls.count == maxURLs {
                return URLExtractionResult(urls: urls, exceededLimit: true)
            }
            seenURLs.insert(dedupeKey)
            urls.append(candidate)
        }
        return URLExtractionResult(urls: urls, exceededLimit: false)
    }

    private static func dedupeKey(for value: String) -> String? {
        guard var components = URLComponents(string: value),
              let scheme = components.scheme?.lowercased(),
              let host = components.host?.lowercased()
        else {
            return nil
        }
        components.scheme = scheme
        components.host = host
        components.fragment = nil

        switch host {
        case "youtube.com", "www.youtube.com", "m.youtube.com":
            if components.path == "/watch" || components.path == "/watch/",
               let videoID = components.queryItems?.first(where: { $0.name == "v" })?.value,
               !videoID.isEmpty {
                return "youtube:\(videoID)"
            }
            if components.path.hasPrefix("/shorts/") {
                let videoID = components.path.replacingOccurrences(of: "/shorts/", with: "").split(separator: "/", maxSplits: 1).first
                return videoID.map { "youtube:\($0)" }
            }
        case "youtu.be":
            let videoID = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            return videoID.isEmpty ? nil : "youtube:\(videoID)"
        case "x.com", "www.x.com", "twitter.com", "www.twitter.com":
            if let statusRange = components.path.range(of: "/status/") {
                let statusID = components.path[statusRange.upperBound...].split(separator: "/", maxSplits: 1).first
                return statusID.map { "x:\($0)" }
            }
        default:
            break
        }
        components.query = nil
        return components.string
    }
}
