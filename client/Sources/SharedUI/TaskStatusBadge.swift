import AppCore
import SwiftUI

public struct TaskStatusBadge: View {
    private let status: JobStatus

    public init(status: JobStatus) {
        self.status = status
    }

    public var body: some View {
        Text(label)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .foregroundStyle(.white)
            .background(color, in: Capsule())
    }

    private var label: String {
        switch status {
        case .created: "已创建"
        case .queued: "排队中"
        case .resolving: "解析中"
        case .resolved: "已解析"
        case .downloading: "下载中"
        case .muxing: "合并中"
        case .storing: "存储中"
        case .completed: "已完成"
        case .failed: "失败"
        case .canceled: "已取消"
        }
    }

    private var color: Color {
        switch status {
        case .completed: .green
        case .failed: .red
        case .canceled: .gray
        case .downloading, .resolving, .resolved, .muxing, .storing: .blue
        case .created, .queued: .orange
        }
    }
}
