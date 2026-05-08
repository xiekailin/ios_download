import AppCore
import Foundation
import Testing

@Test func appControllerBuildsStableServerConfigurationIDs() throws {
    let first = try ServerConfiguration.parseBaseURL("https://cloud.example.com:18767///")
    let same = try ServerConfiguration.parseBaseURL(" https://cloud.example.com:18767 ")
    let differentPort = try ServerConfiguration.parseBaseURL("https://cloud.example.com:18768")
    let differentHost = try ServerConfiguration.parseBaseURL("https://other.example.com:18767")

    #expect(first.url == URL(string: "https://cloud.example.com:18767")!)
    #expect(first.storageID == same.storageID)
    #expect(first.storageID != differentPort.storageID)
    #expect(first.storageID != differentHost.storageID)
    #expect(first.storageID.allSatisfy { $0.isLetter || $0.isNumber || $0 == "-" })
}

@Test func appControllerRejectsInvalidServerConfigurationURLs() {
    #expect(throws: ServerConfiguration.ValidationError.self) {
        try ServerConfiguration.parseBaseURL("")
    }
    #expect(throws: ServerConfiguration.ValidationError.self) {
        try ServerConfiguration.parseBaseURL("ftp://cloud.example.com")
    }
    #expect(throws: ServerConfiguration.ValidationError.self) {
        try ServerConfiguration.parseBaseURL("https:///missing-host")
    }
    #expect(throws: ServerConfiguration.ValidationError.self) {
        try ServerConfiguration.parseBaseURL("https://user:pass@cloud.example.com")
    }
}

@Test func appControllerAllowsCookieManagementOnlyOnHTTPSOrLoopback() {
    #expect(AppController.isSecureCloudCookieBaseURL(URL(string: "https://cloud.example.com")!))
    #expect(AppController.isSecureCloudCookieBaseURL(URL(string: "http://127.0.0.1:18767")!))
    #expect(AppController.isSecureCloudCookieBaseURL(URL(string: "http://localhost:18767")!))
    #expect(!AppController.isSecureCloudCookieBaseURL(URL(string: "http://124.221.197.94:18767")!))
}

@Test func appControllerAcceptsSupportedSourceURLs() {
    #expect(AppController.isValidSupportedSourceURL("https://x.com/demo/status/1"))
    #expect(AppController.isValidSupportedSourceURL("https://x.com/i/status/1"))
    #expect(AppController.isValidSupportedSourceURL("https://twitter.com/demo/status/1"))
    #expect(AppController.isValidSupportedSourceURL("https://twitter.com/i/status/1"))
    #expect(AppController.isValidSupportedSourceURL("https://www.douyin.com/video/123456"))
    #expect(AppController.isValidSupportedSourceURL("https://v.douyin.com/abc123/"))
    #expect(AppController.isValidSupportedSourceURL("https://m.douyin.com/share/video/123456/"))
    #expect(AppController.isValidSupportedSourceURL("https://www.iesdouyin.com/share/video/123456/"))
    #expect(AppController.isValidSupportedSourceURL("https://h5.pipix.com/s/abc123/"))
    #expect(AppController.isValidSupportedSourceURL("https://www.pipix.com/item/123456"))
    #expect(AppController.isValidSupportedSourceURL("https://www.xiaohongshu.com/explore/abcdef"))
    #expect(AppController.isValidSupportedSourceURL("https://xhslink.com/abc123"))
    #expect(AppController.isValidSupportedSourceURL("https://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007.tianma.1-2-2.click"))
    #expect(AppController.isValidSupportedSourceURL("https://www.youtube.com/watch?v=GEFehFHg_os"))
    #expect(AppController.isValidSupportedSourceURL("https://youtu.be/GEFehFHg_os"))
    #expect(AppController.isValidSupportedSourceURL("https://www.youtube.com/shorts/GEFehFHg_os"))
    #expect(!AppController.isValidSupportedSourceURL("https://example.com/demo/status/1"))
    #expect(!AppController.isValidSupportedSourceURL("not-a-url"))
    #expect(!AppController.isValidSupportedSourceURL("https://x.com/home"))
    #expect(!AppController.isValidSupportedSourceURL("https://user:pass@x.com/demo/status/1"))
    #expect(!AppController.isValidSupportedSourceURL("https://x.com:444/demo/status/1"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.douyin.com/video/"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.douyin.com/user/demo"))
    #expect(!AppController.isValidSupportedSourceURL("https://m.douyin.com/share/video/"))
    #expect(!AppController.isValidSupportedSourceURL("https://m.douyin.com/share/video/123456/extra"))
    #expect(!AppController.isValidSupportedSourceURL("https://m.douyin.com/share/video//123456"))
    #expect(!AppController.isValidSupportedSourceURL("https://m.douyin.com/share/video/%2F123456"))
    #expect(!AppController.isValidSupportedSourceURL("https://m.douyin.com/share/video/%5C123456"))
    #expect(!AppController.isValidSupportedSourceURL("https://m.douyin.com/share/video/%252F123456"))
    #expect(!AppController.isValidSupportedSourceURL("https://m.douyin.com/share/video/%255C123456"))
    #expect(!AppController.isValidSupportedSourceURL("https://m.douyin.com/share/video/%2525252F123456"))
    #expect(!AppController.isValidSupportedSourceURL("https://m.douyin.com/share/video/%2525255C123456"))
    #expect(!AppController.isValidSupportedSourceURL("https://m.douyin.com/share/video/%252525252F123456"))
    #expect(!AppController.isValidSupportedSourceURL("https://m.douyin.com/share/video/%252525255C123456"))
    #expect(!AppController.isValidSupportedSourceURL("https://m.douyin.com/user/demo"))
    #expect(!AppController.isValidSupportedSourceURL("https://h5.pipix.com/"))
    #expect(!AppController.isValidSupportedSourceURL("https://h5.pipix.com/s/"))
    #expect(!AppController.isValidSupportedSourceURL("https://h5.pipix.com/s/abc123/extra"))
    #expect(!AppController.isValidSupportedSourceURL("https://h5.pipix.com/s//abc123"))
    #expect(!AppController.isValidSupportedSourceURL("https://h5.pipix.com/s/%2Fabc123"))
    #expect(!AppController.isValidSupportedSourceURL("https://h5.pipix.com/s/%252Fabc123"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.pipix.com/item/"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.pipix.com/item/123456/extra"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.pipix.com/item//123456"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.pipix.com/item/%2F123456"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.pipix.com/item/%255C123456"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.pipix.com/user/demo"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.xiaohongshu.com/user/profile/demo"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.bilibili.com/bangumi/play/ep123"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.youtube.com/watch"))
    #expect(!AppController.isValidSupportedSourceURL("https://youtu.be/"))
    #expect(!AppController.isValidSupportedSourceURL("https://youtu.be/shorts/GEFehFHg_os"))
}
