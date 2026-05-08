import AppCore
import SwiftUI

public struct BackendHeartbeatIndicator: View {
    private let status: BackendHealthStatus

    public init(status: BackendHealthStatus) {
        self.status = status
    }

    public var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(color)
                .frame(width: 9, height: 9)
                .overlay {
                    Circle().stroke(.white.opacity(0.85), lineWidth: 1)
                }
            Text(title)
                .font(.caption2.bold())
                .foregroundStyle(.primary)
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 6)
        .background(.regularMaterial, in: Capsule())
        .shadow(color: .black.opacity(0.12), radius: 8, y: 3)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("本地后端状态")
        .accessibilityValue(accessibilityValue)
        .help(helpText)
    }

    private var color: Color {
        switch status {
        case .healthy:
            .green
        case .unknown, .unhealthy:
            .red
        }
    }

    private var title: String {
        switch status {
        case .healthy:
            "后端正常"
        case .unknown:
            "检查中"
        case .unhealthy:
            "后端断开"
        }
    }

    private var accessibilityValue: String {
        switch status {
        case .healthy:
            "已连接"
        case .unknown:
            "检查中"
        case .unhealthy:
            "未连接"
        }
    }

    private var helpText: String {
        switch status {
        case .healthy:
            "本地后端已连接"
        case .unknown:
            "正在检查本地后端"
        case .unhealthy:
            "本地后端未连接"
        }
    }
}
