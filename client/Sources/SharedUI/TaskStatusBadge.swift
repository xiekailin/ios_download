import AppCore
import SwiftUI

public struct TaskStatusBadge: View {
    private let status: JobStatus

    public init(status: JobStatus) {
        self.status = status
    }

    public var body: some View {
        #if os(macOS)
        Label(label, systemImage: icon)
            .font(.caption2.weight(.semibold))
            .labelStyle(.titleAndIcon)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .foregroundStyle(color)
            .background(color.opacity(0.12), in: Capsule())
            .overlay(Capsule().stroke(color.opacity(0.24), lineWidth: 1))
            .fixedSize(horizontal: true, vertical: false)
        #else
        Text(label)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .foregroundStyle(foregroundColor)
            .background(color, in: Capsule())
            .overlay(Capsule().stroke(color.opacity(0.42), lineWidth: 1))
            .fixedSize(horizontal: true, vertical: false)
        #endif
    }

    private var label: String {
        status.presentationTitle
    }

    private var icon: String {
        status.presentationSystemImage
    }

    private var foregroundColor: Color {
        switch status {
        case .completed, .downloading, .resolving, .resolved, .muxing, .storing, .created, .queued, .paused:
            Color(red: 0.04, green: 0.045, blue: 0.052)
        case .failed, .canceled:
            .white
        }
    }

    private var color: Color {
        switch status {
        #if os(macOS)
        case .completed: .green
        case .failed: .red
        case .canceled: .secondary
        case .paused: .secondary
        case .downloading, .resolving, .resolved, .muxing, .storing: .accentColor
        case .created, .queued: .orange
        #else
        case .completed: Color(red: 0.50, green: 0.78, blue: 0.60)
        case .failed: Color(red: 0.70, green: 0.22, blue: 0.24)
        case .canceled: Color(red: 0.40, green: 0.43, blue: 0.46)
        case .paused: Color(red: 0.72, green: 0.74, blue: 0.76)
        case .downloading, .resolving, .resolved, .muxing, .storing: Color(red: 0.58, green: 0.88, blue: 0.98)
        case .created, .queued: Color(red: 0.91, green: 0.76, blue: 0.48)
        #endif
        }
    }
}
