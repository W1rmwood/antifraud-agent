import AntifraudCore
import SwiftUI

// Общие кирпичики экранов. Цвета — системные: тёмная и светлая темы работают сами.

enum Tone {
    case red, orange, green, agent, neutral

    var color: Color {
        switch self {
        case .red: .red
        case .orange: .orange
        case .green: .green
        case .agent: .indigo
        case .neutral: .secondary
        }
    }
}

/// Карточка с заголовком и иконкой.
struct Card<Content: View>: View {
    var title: String?
    var systemImage: String?
    var tint: Color = .primary
    var trailing: AnyView?
    @ViewBuilder var content: Content

    init(
        _ title: String? = nil, systemImage: String? = nil, tint: Color = .primary, trailing: AnyView? = nil,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.systemImage = systemImage
        self.tint = tint
        self.trailing = trailing
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if title != nil || systemImage != nil {
                HStack(spacing: 8) {
                    if let systemImage {
                        Image(systemName: systemImage).foregroundStyle(tint).accessibilityHidden(true)
                    }
                    if let title { Text(title).font(.headline) }
                    Spacer(minLength: 0)
                    trailing
                }
            }
            content
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

/// Плашка-метка.
struct Chip: View {
    var text: String
    var tone: Tone = .neutral
    var systemImage: String?

    init(_ text: String, tone: Tone = .neutral, systemImage: String? = nil) {
        self.text = text
        self.tone = tone
        self.systemImage = systemImage
    }

    var body: some View {
        HStack(spacing: 4) {
            if let systemImage { Image(systemName: systemImage).imageScale(.small).accessibilityHidden(true) }
            Text(text)
        }
        .font(.caption)
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .foregroundStyle(tone == .neutral ? Color.primary : tone.color)
        .background((tone == .neutral ? Color.secondary : tone.color).opacity(0.15), in: Capsule())
    }
}

/// Строки, переносящиеся по ширине: метки, фразы.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews: subviews)
        let height = rows.last.map { $0.y + $0.height } ?? 0
        let width = rows.map(\.width).max() ?? 0
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for row in arrange(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for i in row.items {
                let size = subviews[i].sizeThatFits(.unspecified)
                subviews[i].place(at: CGPoint(x: x, y: bounds.minY + row.y), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
        }
    }

    private struct Row { var items: [Int] = []; var y: CGFloat = 0; var width: CGFloat = 0; var height: CGFloat = 0 }

    private func arrange(width: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = []
        var row = Row()
        var y: CGFloat = 0
        for i in subviews.indices {
            let size = subviews[i].sizeThatFits(.unspecified)
            if !row.items.isEmpty, row.width + spacing + size.width > width {
                rows.append(row)
                y += row.height + spacing
                row = Row(y: y)
            }
            row.width += (row.items.isEmpty ? 0 : spacing) + size.width
            row.height = max(row.height, size.height)
            row.items.append(i)
        }
        if !row.items.isEmpty { rows.append(row) }
        return rows
    }
}

/// Метки одной строкой с переносом.
struct Chips: View {
    var items: [String]
    var tone: Tone = .neutral

    var body: some View {
        FlowLayout {
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in Chip(item, tone: tone) }
        }
    }
}

/// Цветная точка состояния.
struct Dot: View {
    var color: Color
    var body: some View {
        Circle().fill(color).frame(width: 8, height: 8).accessibilityHidden(true)
    }
}

/// Плашка-объявление над содержимым.
struct Banner: View {
    var text: String
    var tone: Tone = .neutral
    var systemImage: String = "info.circle"

    var body: some View {
        Label(text, systemImage: systemImage)
            .font(.subheadline)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .foregroundStyle(tone == .neutral ? Color.primary : tone.color)
            .background((tone == .neutral ? Color.secondary : tone.color).opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
    }
}

/// «Свойство: значение» в отчёте и карточках.
struct PropRow<Value: View>: View {
    var label: String
    @ViewBuilder var value: Value

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(.secondary)
            Spacer(minLength: 12)
            value.multilineTextAlignment(.trailing)
        }
    }
}

/// Полоса уверенности.
struct ConfidenceBar: View {
    var value: Double
    var tint: Color = .indigo

    var body: some View {
        ProgressView(value: min(max(value, 0), 1))
            .tint(tint)
            .accessibilityLabel("Уверенность \(Int((value * 100).rounded()))%")
    }
}

/// Команда починки сервера с кнопкой «Скопировать».
struct FixCommand: View {
    var command: String
    @State private var copied = false

    var body: some View {
        HStack(alignment: .top) {
            Text(command)
                .font(.system(.footnote, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button(copied ? "Скопировано" : "Скопировать") {
                Platform.copy(command)
                copied = true
                Task {
                    try? await Task.sleep(for: .seconds(1.5))
                    copied = false
                }
            }
            .font(.caption)
            .buttonStyle(.bordered)
        }
        .padding(8)
        .background(.background.tertiary, in: RoundedRectangle(cornerRadius: 8))
    }
}

/// Ошибка загрузки справочника.
struct ErrorText: View {
    var text: String
    var body: some View {
        Label(text, systemImage: "exclamationmark.triangle").foregroundStyle(.red).font(.subheadline)
    }
}

extension View {
    /// Поле номера: цифровая клавиатура на iPhone.
    func phoneInput() -> some View {
        #if os(iOS)
        return self.keyboardType(.phonePad).textContentType(.telephoneNumber)
        #else
        return self
        #endif
    }

    /// Поле адреса или кода: без автозамены и заглавных букв.
    func plainInput() -> some View {
        #if os(iOS)
        return self.keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
        #else
        return self.autocorrectionDisabled()
        #endif
    }

    func numberInput() -> some View {
        #if os(iOS)
        return self.keyboardType(.numberPad).textContentType(.oneTimeCode)
        #else
        return self
        #endif
    }

    func inlineTitle() -> some View {
        #if os(iOS)
        return self.navigationBarTitleDisplayMode(.inline)
        #else
        return self
        #endif
    }
}
