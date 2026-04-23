import AppCore
import SwiftUI

public struct DownloadProgressDetails: View {
    private let job: Job

    public init(job: Job) {
        self.job = job
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ProgressView(value: job.progressFraction, total: 1)
            if let metricsText = job.metricsText {
                Text(metricsText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text(job.secondaryStatusText)
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }
}
