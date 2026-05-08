import AppCore
import Foundation
import Testing

@Test func parsesDownloadDeepLinkWithSupportedURL() {
    let action = DeepLinkParser.parse(URL(string: "xdownloader://download?url=https%3A%2F%2Fx.com%2Fdemo%2Fstatus%2F1")!)

    #expect(action == .download("https://x.com/demo/status/1"))
}

@Test func parsesAudioDeepLinkWithSupportedURL() {
    let action = DeepLinkParser.parse(URL(string: "xdownloader://audio?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DGEFehFHg_os")!)

    #expect(action == .audio("https://www.youtube.com/watch?v=GEFehFHg_os"))
}

@Test func parsesNewPlatformDeepLinks() {
    let douyinURL = URL(string: "xdownloader://download?url=https%3A%2F%2Fm.douyin.com%2Fshare%2Fvideo%2F123456%2F")!
    let pipixiaURL = URL(string: "xdownloader://audio?url=https%3A%2F%2Fh5.pipix.com%2Fs%2Fabc123%2F")!

    #expect(DeepLinkParser.parse(douyinURL) == .download("https://m.douyin.com/share/video/123456/"))
    #expect(DeepLinkParser.parse(pipixiaURL) == .audio("https://h5.pipix.com/s/abc123/"))
}

@Test func rejectsUnsupportedDeepLinks() {
    #expect(DeepLinkParser.parse(URL(string: "https://x.com/demo/status/1")!) == nil)
    #expect(DeepLinkParser.parse(URL(string: "xdownloader://settings")!) == nil)
    #expect(DeepLinkParser.parse(URL(string: "xdownloader://download")!) == nil)
    #expect(DeepLinkParser.parse(URL(string: "xdownloader://download?url=https%3A%2F%2Fexample.com%2Fdemo")!) == nil)
    #expect(DeepLinkParser.parse(URL(string: "xdownloader://download?url=file%3A%2F%2F%2Ftmp%2Fvideo.mp4")!) == nil)
    #expect(DeepLinkParser.parse(URL(string: "xdownloader://download?url=javascript%3Aalert(1)")!) == nil)
}
