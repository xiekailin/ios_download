import Foundation

public enum DeepLinkAction: Equatable, Sendable {
    case download(String)
    case audio(String)
}

public enum DeepLinkSubmissionMode: Equatable, Sendable {
    case download
    case audio
}

public enum DeepLinkParser {
    public static func parse(_ url: URL) -> DeepLinkAction? {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme?.lowercased() == "xdownloader"
        else {
            return nil
        }
        let action = components.host?.lowercased()
        guard let sourceURL = components.queryItems?.first(where: { $0.name == "url" })?.value,
              AppController.isValidSupportedSourceURL(sourceURL)
        else {
            return nil
        }
        switch action {
        case "download":
            return .download(sourceURL)
        case "audio":
            return .audio(sourceURL)
        default:
            return nil
        }
    }
}
