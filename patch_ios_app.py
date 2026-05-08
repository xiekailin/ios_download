import sys
import re

with open("client/Apps/XDownloaderiOS/XDownloaderiOSApp.swift", "r") as f:
    content = f.read()

# Regex to find heroInputCard
pattern = re.compile(r'(    private var heroInputCard: some View \{.*?\n    \})', re.DOTALL)
match = pattern.search(content)

if not match:
    print("Could not find heroInputCard")
    sys.exit(1)

new_hero_input_card = """    private var heroInputCard: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("粘贴链接，即刻解析")
                        .font(.system(size: 28, weight: .black, design: .rounded))
                        .foregroundStyle(themeMode.textPrimary)
                    Text("支持 X / 抖音 / 皮皮虾 / 小红书 / Bilibili / YouTube")
                        .font(.footnote.weight(.medium))
                        .foregroundStyle(themeMode.textSecondary)
                }
                Spacer(minLength: 12)
                Button {
                    selectedConsoleTab = .settings
                } label: {
                    Image(systemName: "slider.horizontal.3")
                        .font(.system(size: 20, weight: .semibold))
                        .foregroundStyle(themeMode.primaryAccent)
                        .frame(width: 50, height: 50)
                        .background(themeMode.surfaceElevated, in: Circle())
                        .overlay(Circle().stroke(themeMode.border, lineWidth: 1))
                        .shadow(color: .black.opacity(0.1), radius: 8, x: 0, y: 4)
                }
                .accessibilityLabel("设置")
            }

            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    Image(systemName: "link")
                        .font(.headline.weight(.semibold))
                        .foregroundStyle(themeMode.primaryAccent)
                    TextField("https://...", text: $store.draftURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .font(.body.monospaced())
                        .foregroundStyle(themeMode.textPrimary)
                        .accessibilityLabel("下载链接")
                    
                    if !store.draftURL.isEmpty {
                        Button(action: clearDraftURL) {
                            Image(systemName: "xmark.circle.fill")
                                .font(.title3.weight(.semibold))
                                .foregroundStyle(themeMode.textMuted.opacity(0.8))
                                .frame(width: 44, height: 44)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("清空链接")
                    }
                    
                    Button(action: pasteDraftURL) {
                        Image(systemName: "doc.on.clipboard.fill")
                            .font(.title3.weight(.semibold))
                            .foregroundStyle(themeMode.primaryAccent)
                            .frame(width: 44, height: 44)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("粘贴链接")
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 14)
                .background(themeMode.surfaceInset)
                .clipShape(RoundedRectangle(cornerRadius: 18))
                .overlay(
                    RoundedRectangle(cornerRadius: 18)
                        .stroke(store.draftURL.isEmpty ? themeMode.border : themeMode.primaryAccent.opacity(0.5), lineWidth: 1.5)
                )
            }

            HStack(spacing: 12) {
                Button {
                    preferredDeepLinkMode = .download
                    Task { await submitCurrentInput() }
                } label: {
                    HStack(spacing: 8) {
                        if store.isLoading, preferredDeepLinkMode == .download {
                            ProgressView()
                                .tint(.black.opacity(0.8))
                        } else {
                            Image(systemName: "arrow.down.circle.fill")
                                .font(.title3)
                        }
                        Text(store.isLoading && preferredDeepLinkMode == .download ? "处理中" : "开始下载")
                            .font(.headline.weight(.bold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                }
                .buttonStyle(ConsolePrimaryButtonStyle(theme: themeMode))
                .disabled(!hasServerSettings || store.isLoading || store.draftURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                Button {
                    preferredDeepLinkMode = .audio
                    Task { await submitCurrentInput() }
                } label: {
                    HStack(spacing: 8) {
                        if store.isLoading, preferredDeepLinkMode == .audio {
                            ProgressView()
                                .tint(themeMode.textPrimary)
                        } else {
                            Image(systemName: "music.note")
                                .font(.title3)
                        }
                        Text(store.isLoading && preferredDeepLinkMode == .audio ? "提取中" : "提取音频")
                            .font(.headline.weight(.bold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                }
                .buttonStyle(ConsoleSecondaryButtonStyle(theme: themeMode))
                .disabled(!hasServerSettings || store.isLoading || store.draftURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }

            Button {
                Task {
                    successMessage = nil
                    autoSaveFailedJobIDs.removeAll()
                    await controller.refreshJobs(store: store)
                }
            } label: {
                Label("刷新任务状态", systemImage: "arrow.clockwise")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
            }
            .buttonStyle(ConsoleSecondaryButtonStyle(theme: themeMode))
            .disabled(store.isLoading)
        }
        .padding(24)
        .background(
            RoundedRectangle(cornerRadius: 32, style: .continuous)
                .fill(themeMode.surface.opacity(0.95))
                .shadow(color: themeMode == .premium ? .black.opacity(0.15) : themeMode.primaryAccent.opacity(0.1), radius: 20, x: 0, y: 10)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 32, style: .continuous)
                .stroke(themeMode.border, lineWidth: 1)
        )
    }"""

content = content[:match.start()] + new_hero_input_card + content[match.end():]

with open("client/Apps/XDownloaderiOS/XDownloaderiOSApp.swift", "w") as f:
    f.write(content)

print("Patch applied successfully")
