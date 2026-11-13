import AntifraudCore
import SwiftUI

// Лента реплик — перенос ui/web/src/components/Transcript.jsx: фразы с подсветкой признаков
// и строки агента между ними («определил схему», «этап N»).

struct TranscriptLineView: View {
    var line: TranscriptLine
    var showWho: Bool

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(Format.clock(line.tStart)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 4) {
                if showWho {
                    Text(Format.speaker(line.speaker)).font(.caption.weight(.semibold)).foregroundStyle(whoColor)
                }
                highlighted.fixedSize(horizontal: false, vertical: true)
                let labels = line.flagLabels
                if !labels.isEmpty { Chips(items: labels, tone: .red) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(Format.speaker(line.speaker)), \(Format.clock(line.tStart)): \(line.text)")
    }

    private var whoColor: Color {
        switch line.speaker {
        case .caller: .red
        case .user: .blue
        case .unknown: .secondary
        }
    }

    /// Текст с подсветкой: красное — правило или требование, фиолетовое — основание схемы.
    private var highlighted: Text {
        var out = AttributedString()
        for s in line.segments {
            var piece = AttributedString(s.text)
            switch s.kind {
            case .rule?, .demand?:
                piece.backgroundColor = Color.red.opacity(0.22)
            case .other?:
                piece.backgroundColor = Color.orange.opacity(0.22)
            case .evidence?:
                piece.backgroundColor = Color.indigo.opacity(0.18)
            case nil:
                break
            }
            out += piece
        }
        return Text(out)
    }
}

struct AgentNoteView: View {
    var note: InlineNote

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(Format.clock(note.at)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            if note.kind == .scheme, let scheme = note.scheme {
                Label {
                    Text("Агент определил схему \(scheme.schemeName), \(scheme.percent)%")
                        + Text(note.stage.map { ", этап \($0.stageN): \($0.stageName)" } ?? "").foregroundColor(.secondary)
                } icon: {
                    Image(systemName: "scope")
                }
                .foregroundStyle(.indigo)
            } else if let stage = note.stage {
                Label("Этап \(stage.stageN): \(stage.stageName)", systemImage: "list.number").foregroundStyle(.secondary)
            }
        }
        .font(.caption)
        .padding(.vertical, 2)
    }
}

/// Реплики и заметки агента одним списком, в порядке разговора.
struct TranscriptItems: View {
    var lines: [TranscriptLine]
    var notes: [InlineNote]

    var body: some View {
        // Заметки до первой реплики (afterSeq = -1).
        ForEach(Array(notes.filter { $0.afterSeq == -1 }.enumerated()), id: \.offset) { _, n in AgentNoteView(note: n) }
        ForEach(Array(lines.enumerated()), id: \.element.seq) { i, line in
            TranscriptLineView(line: line, showWho: i == 0 || lines[i - 1].speaker != line.speaker)
                .id(line.seq)
            ForEach(Array(notes.filter { $0.afterSeq == line.seq }.enumerated()), id: \.offset) { _, n in AgentNoteView(note: n) }
        }
    }
}

/// Пока не прозвучало ни фразы: показать, что агент жив и слушает.
struct ListeningView: View {
    var text: String
    var body: some View {
        HStack(spacing: 10) {
            ProgressView()
            Text(text).foregroundStyle(.secondary)
        }
        .padding(.vertical, 8)
    }
}
