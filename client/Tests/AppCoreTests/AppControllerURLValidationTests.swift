import AppCore
import Testing

@Test func appControllerAcceptsSupportedSourceURLs() {
    #expect(AppController.isValidSupportedSourceURL("https://x.com/demo/status/1"))
    #expect(AppController.isValidSupportedSourceURL("https://twitter.com/demo/status/1"))
    #expect(AppController.isValidSupportedSourceURL("https://www.douyin.com/video/123456"))
    #expect(AppController.isValidSupportedSourceURL("https://v.douyin.com/abc123/"))
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
    #expect(!AppController.isValidSupportedSourceURL("https://www.douyin.com/user/demo"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.xiaohongshu.com/user/profile/demo"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.bilibili.com/bangumi/play/ep123"))
    #expect(!AppController.isValidSupportedSourceURL("https://www.youtube.com/watch"))
    #expect(!AppController.isValidSupportedSourceURL("https://youtu.be/"))
    #expect(!AppController.isValidSupportedSourceURL("https://youtu.be/shorts/GEFehFHg_os"))
}
