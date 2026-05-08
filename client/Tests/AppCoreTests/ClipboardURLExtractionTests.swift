import AppCore
import Testing

@Test func extractsPureSupportedClipboardURL() {
    #expect(ClipboardURLExtractor.firstSupportedURL(in: "https://x.com/demo/status/1") == "https://x.com/demo/status/1")
}

@Test func extractsFirstSupportedClipboardURLFromSurroundingText() {
    let text = "这个视频不错 https://www.youtube.com/watch?v=GEFehFHg_os 可以下载"

    #expect(ClipboardURLExtractor.firstSupportedURL(in: text) == "https://www.youtube.com/watch?v=GEFehFHg_os")
}

@Test func extractsFirstSupportedClipboardURLFromMultilineText() {
    let text = "第一行\nhttps://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007\n第三行"

    #expect(ClipboardURLExtractor.firstSupportedURL(in: text) == "https://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007")
}

@Test func ignoresUnsupportedClipboardURLAndPlainText() {
    #expect(ClipboardURLExtractor.firstSupportedURL(in: "https://example.com/demo/status/1") == nil)
    #expect(ClipboardURLExtractor.firstSupportedURL(in: "只是一段普通文字") == nil)
}

@Test func prefersFirstSupportedClipboardURLWhenMultipleExist() {
    let text = "https://example.com/skip https://v.douyin.com/abc123/ https://x.com/demo/status/2"

    #expect(ClipboardURLExtractor.firstSupportedURL(in: text) == "https://v.douyin.com/abc123/")
}

@Test func extractsMobileDouyinAndPipixiaClipboardURLs() {
    #expect(ClipboardURLExtractor.firstSupportedURL(in: "抖音视频 https://m.douyin.com/share/video/123456/ 复制此链接") == "https://m.douyin.com/share/video/123456/")
    #expect(ClipboardURLExtractor.firstSupportedURL(in: "9.46 复制打开抖音，看看【傅立青的作品】被淋了，为什么还笑啊？？  https://v.douyin.com/yjaQ3bMm4us/ J@i.Ch 01/28 Rkc:/") == "https://v.douyin.com/yjaQ3bMm4us/")
    #expect(ClipboardURLExtractor.firstSupportedURL(in: "1.71 复制打开抖音，看看【兔一妈妈的作品】我的妈妈美如鲜花 # 人类幼崽迷之角度 # 亲子日... https://v.douyin.com/vaGFzBkNa_U/ 07/27 e@o.dN jPk:/") == "https://v.douyin.com/vaGFzBkNa_U/")
    #expect(ClipboardURLExtractor.firstSupportedURL(in: "皮皮虾 https://h5.pipix.com/s/abc123/ 这个太好笑了") == "https://h5.pipix.com/s/abc123/")
}

@Test func extractsAllSupportedClipboardURLsInOrder() {
    let text = """
    第一条 https://x.com/demo/status/1
    普通文字 https://example.com/skip
    第二条 https://www.youtube.com/watch?v=GEFehFHg_os
    第三条 https://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007
    """

    #expect(ClipboardURLExtractor.supportedURLs(in: text) == [
        "https://x.com/demo/status/1",
        "https://www.youtube.com/watch?v=GEFehFHg_os",
        "https://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007",
    ])
}

@Test func extractsSupportedClipboardURLsOnlyOnce() {
    let text = "https://x.com/demo/status/1\nhttps://x.com/demo/status/1\nhttps://youtu.be/GEFehFHg_os。"

    #expect(ClipboardURLExtractor.supportedURLs(in: text) == [
        "https://x.com/demo/status/1",
        "https://youtu.be/GEFehFHg_os",
    ])
}

@Test func deduplicatesEquivalentSupportedClipboardURLs() {
    let text = """
    https://youtu.be/GEFehFHg_os
    https://www.youtube.com/watch?v=GEFehFHg_os&t=30
    https://x.com/demo/status/1?utm_source=copy
    https://x.com/demo/status/1
    """

    #expect(ClipboardURLExtractor.supportedURLs(in: text) == [
        "https://youtu.be/GEFehFHg_os",
        "https://x.com/demo/status/1?utm_source=copy",
    ])
}

@Test func limitsSupportedClipboardURLExtraction() {
    let text = (1...3).map { "https://x.com/demo/status/\($0)" }.joined(separator: "\n")

    let result = ClipboardURLExtractor.supportedURLs(in: text, maxURLs: 2)

    #expect(result.urls == ["https://x.com/demo/status/1", "https://x.com/demo/status/2"])
    #expect(result.exceededLimit)
}

@Test func trimsTrailingPunctuationAroundClipboardURL() {
    #expect(ClipboardURLExtractor.firstSupportedURL(in: "下载：https://x.com/demo/status/1。") == "https://x.com/demo/status/1")
    #expect(ClipboardURLExtractor.firstSupportedURL(in: "(https://youtu.be/GEFehFHg_os)") == "https://youtu.be/GEFehFHg_os")
}

@Test func rejectsUnsafeClipboardURLForms() {
    #expect(ClipboardURLExtractor.firstSupportedURL(in: "https://user:pass@x.com/demo/status/1") == nil)
    #expect(ClipboardURLExtractor.firstSupportedURL(in: "https://x.com:444/demo/status/1") == nil)
}
