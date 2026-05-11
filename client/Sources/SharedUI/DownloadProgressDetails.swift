import AppCore
import SwiftUI

public struct DownloadProgressDetails: View {
    private let job: Job

    public init(job: Job) {
        self.job = job
    }

    public var body: some View {
        #if os(macOS)
        VStack(alignment: .leading, spacing: 6) {
            ProgressView(value: job.progressFraction, total: 1)
                .tint(progressTint)
            if let metricsText = job.metricsText {
                Text(metricsText)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            statusText
        }
        #else
        VStack(alignment: .leading, spacing: 7) {
            ProgressView(value: job.progressFraction, total: 1)
                .tint(Color(red: 0.48, green: 0.82, blue: 0.94))
            if let metricsText = job.metricsText {
                Text(metricsText)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            statusText
        }
        #endif
    }

    @ViewBuilder
    private var statusText: some View {
        if job.displayErrorText == nil {
            Text(job.secondaryStatusText)
                .font(statusFont)
                .foregroundStyle(.secondary)
        } else {
            Text(job.secondaryStatusText)
                .font(statusFont)
                .foregroundStyle(.red)
        }
    }

    private var statusFont: Font {
        #if os(macOS)
        .caption
        #else
        .footnote
        #endif
    }

    private var progressTint: Color {
        switch job.status {
        case .completed:
            .green
        case .failed:
            .red
        case .canceled, .paused:
            .secondary
        case .created, .queued:
            .orange
        case .resolving, .resolved, .downloading, .muxing, .storing:
            .accentColor
        }
    }
}
