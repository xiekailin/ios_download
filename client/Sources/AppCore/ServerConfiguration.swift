import Foundation

public struct ServerConfiguration: Sendable, Equatable {
    public struct ValidationError: LocalizedError, Equatable {
        public let errorDescription: String?

        public init(_ errorDescription: String) {
            self.errorDescription = errorDescription
        }
    }

    public let url: URL
    public let storageID: String

    public static func parseBaseURL(_ value: String) throws -> ServerConfiguration {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw ValidationError("请输入服务器地址。")
        }
        guard var components = URLComponents(string: trimmed), let scheme = components.scheme?.lowercased(), scheme == "http" || scheme == "https" else {
            throw ValidationError("服务器地址必须以 http:// 或 https:// 开头。")
        }
        guard let host = components.host, !host.isEmpty else {
            throw ValidationError("服务器地址缺少域名或 IP。")
        }
        guard components.user == nil, components.password == nil else {
            throw ValidationError("服务器地址不能包含用户名或密码。")
        }
        components.scheme = scheme
        components.path = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if !components.path.isEmpty {
            components.path = "/" + components.path
        }
        components.query = nil
        components.fragment = nil
        guard let url = components.url else {
            throw ValidationError("服务器地址格式不正确。")
        }
        return ServerConfiguration(url: url, storageID: storageID(for: components))
    }

    private static func storageID(for components: URLComponents) -> String {
        let value = [
            components.scheme?.lowercased(),
            components.host?.lowercased(),
            components.port.map(String.init),
            components.path.isEmpty ? nil : components.path.lowercased(),
        ]
        .compactMap { $0 }
        .joined(separator: "-")
        let safe = value.map { character in
            character.isLetter || character.isNumber ? character : "-"
        }
        return String(safe).split(separator: "-").joined(separator: "-")
    }
}
