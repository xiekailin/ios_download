import AppCore
import Foundation
import Security

private struct StoredRegistration: Codable {
    let deviceID: String
    let tokenType: String
}

public actor LocalRegistrationRepository: RegistrationStore {
    private let storageURL: URL
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    private let service = "xdownloader.device-registration"
    private let account: String

    public init(storageURL: URL? = nil, account: String = "default") {
        let defaultURL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "xdownloader_device.json")
        self.storageURL = storageURL ?? defaultURL
        self.account = account
    }

    public func loadRegistration() throws -> DeviceRegistration? {
        guard FileManager.default.fileExists(atPath: storageURL.path()) else {
            return nil
        }
        let data = try Data(contentsOf: storageURL)
        let stored = try decoder.decode(StoredRegistration.self, from: data)
        guard let accessToken = try loadAccessToken() else {
            return nil
        }
        return DeviceRegistration(deviceID: stored.deviceID, accessToken: accessToken, tokenType: stored.tokenType)
    }

    public func saveRegistration(_ registration: DeviceRegistration) throws {
        try FileManager.default.createDirectory(
            at: storageURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let stored = StoredRegistration(deviceID: registration.deviceID, tokenType: registration.tokenType)
        let data = try encoder.encode(stored)
        try data.write(to: storageURL, options: .atomic)
        try saveAccessToken(registration.accessToken)
    }

    private func loadAccessToken() throws -> String? {
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
            kSecReturnData: true,
            kSecMatchLimit: kSecMatchLimitOne,
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess, let data = result as? Data, let token = String(data: data, encoding: .utf8) else {
            throw RegistrationStoreError.keychainReadFailed(status)
        }
        return token
    }

    private func saveAccessToken(_ token: String) throws {
        let data = Data(token.utf8)
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
        ]
        let attributes: [CFString: Any] = [
            kSecValueData: data,
        ]
        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecItemNotFound {
            var createQuery = query
            createQuery[kSecValueData] = data
            let createStatus = SecItemAdd(createQuery as CFDictionary, nil)
            guard createStatus == errSecSuccess else {
                throw RegistrationStoreError.keychainWriteFailed(createStatus)
            }
            return
        }
        guard updateStatus == errSecSuccess else {
            throw RegistrationStoreError.keychainWriteFailed(updateStatus)
        }
    }
}

public enum RegistrationStoreError: LocalizedError {
    case keychainReadFailed(OSStatus)
    case keychainWriteFailed(OSStatus)

    public var errorDescription: String? {
        switch self {
        case let .keychainReadFailed(status):
            return "读取设备令牌失败（\(status)）。"
        case let .keychainWriteFailed(status):
            return "保存设备令牌失败（\(status)）。"
        }
    }
}
