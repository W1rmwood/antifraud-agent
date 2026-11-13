import AntifraudCore
import SwiftUI

// Карточки агента на экране звонка — перенос ui/web/src/components/AgentPanel.jsx.

/// Вердикт: главное, что человек должен увидеть во время звонка.
struct VerdictCard: View {
    var state: CallState
    var live: Bool

    var body: some View {
        switch state.verdict {
        case .none:
            Card {
                HStack(spacing: 8) {
                    Dot(color: live || state.ended ? .green : .secondary)
                    Text(live ? "Признаков мошенничества нет" : state.ended ? "Признаков мошенничества не было" : "Звонок не разобран")
                        .font(.title3.weight(.semibold))
                }
                Text(
                    live ? "Агент слушает разговор и предупредит, если появятся признаки развода."
                        : state.ended ? "Агент не нашёл ни требований, ни признаков развода."
                        : "Агент остановился раньше, чем успел что-то понять."
                )
                .foregroundStyle(.secondary)
            }
            .accessibilityElement(children: .combine)
        case .scheme, .warning:
            let tone: Color = state.verdict == .scheme ? .red : .orange
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill").accessibilityHidden(true)
                    Text(title).font(.title2.weight(.bold))
                    Spacer(minLength: 0)
                    if let scheme = state.scheme {
                        Text("\(scheme.percent)%").font(.title2.weight(.bold)).monospacedDigit()
                            .accessibilityLabel("уверенность \(scheme.percent)%")
                    }
                }
                if let scheme = state.scheme {
                    Text(scheme.schemeName).font(.headline)
                } else if let last = state.alarms.last {
                    Text(last.labels.joined(separator: ", ")).font(.headline)
                }
                if state.scheme != nil || !state.alarms.isEmpty {
                    Text("Ничего не называйте и не переводите.").font(.subheadline.weight(.semibold))
                }
            }
            .foregroundStyle(.white)
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(tone.gradient, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .accessibilityElement(children: .combine)
        }
    }

    private var title: String {
        if state.verdict == .scheme { return "Похоже на мошенничество" }
        if !state.alarms.isEmpty { return "Есть признаки мошенничества" }
        let history = state.warnings.first { $0.kind == .history }
        return history?.schemes.isEmpty == false ? "С этого номера уже звонили мошенники" : "Номер уже звонил"
    }
}

/// «Положите трубку…» — заметно, но не крупнее подсказки.
struct HangupCard: View {
    var text: String

    var body: some View {
        Label(text, systemImage: "phone.down.fill")
            .font(.body.weight(.semibold))
            .foregroundStyle(.red)
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.red.opacity(0.12), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .accessibilityLabel("Выход: \(text)")
    }
}

/// «Что сказать»: подсказки агента, можно листать уже пришедшие.
struct HintCard: View {
    var hints: [SuggestionItem]
    var showProvoke: Bool
    /// Номер выбранной подсказки; nil — следить за последней.
    @State private var pinned: Int?

    var body: some View {
        let shown = showProvoke ? hints : hints.filter { $0.kind != .provoke }
        let hidden = hints.count - shown.count
        let i = pinned.flatMap { $0 < shown.count ? $0 : nil } ?? shown.count - 1
        let hint = shown.indices.contains(i) ? shown[i] : nil
        Card(
            "Что сказать", systemImage: "text.bubble", tint: .indigo,
            trailing: hint.map { AnyView(Chip(Format.suggestionLabel(kind: $0.kind, tactic: $0.tactic), tone: .agent)) }
        ) {
            if let hint {
                Text(hint.text).font(.title3.weight(.medium)).fixedSize(horizontal: false, vertical: true)
            } else {
                Text("Подсказка появится, когда агент поймёт, что за схема.").foregroundStyle(.secondary)
            }
            if shown.count > 1 {
                HStack {
                    Text(i == shown.count - 1 ? "Последняя подсказка" : "Пришла в \(Format.clock(hint?.at))")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button { pinned = max(0, i - 1) } label: { Image(systemName: "chevron.left") }
                        .disabled(i <= 0)
                        .accessibilityLabel("Предыдущая подсказка")
                    Text("\(i + 1) из \(shown.count)").font(.caption).monospacedDigit()
                    Button { pinned = min(shown.count - 1, i + 1) } label: { Image(systemName: "chevron.right") }
                        .disabled(i >= shown.count - 1)
                        .accessibilityLabel("Следующая подсказка")
                }
                .buttonStyle(.borderless)
            }
            if hidden > 0 {
                Text("Грубых подсказок скрыто: \(hidden). Включаются в настройках нового звонка.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        // Пришла новая — снова показываем последнюю.
        .onChange(of: shown.count) { pinned = nil }
    }
}

/// Плашки-предупреждения.
struct SignsCard: View {
    var warnings: [WarningItem]
    var onJump: ((Int) -> Void)?

    var body: some View {
        let alarms = warnings.filter { $0.kind != .history }
        Card(
            "Признаки", systemImage: "exclamationmark.triangle", tint: .red,
            trailing: alarms.isEmpty ? nil : AnyView(Chip("\(alarms.count)", tone: .red))
        ) {
            if alarms.isEmpty {
                Text("Пока ни одного.").foregroundStyle(.secondary)
            } else {
                ForEach(Array(alarms.enumerated()), id: \.offset) { _, w in
                    Button {
                        if let seq = w.lineSeq { onJump?(seq) }
                    } label: {
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Text(Format.clock(w.phraseAt ?? w.at)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(w.title).font(.subheadline.weight(.semibold))
                                if let q = w.quotes.last { Text("«\(q)»").font(.subheadline) }
                                if w.kind == .other { Text(w.text).font(.subheadline).foregroundStyle(.secondary) }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(w.lineSeq == nil || onJump == nil)
                }
            }
        }
    }
}

/// Этапы схемы: пройденные, текущий, впереди.
struct Stepper: View {
    var stages: [StageInfo]
    var current: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(stages, id: \.n) { s in
                let state = current.map { s.n < $0 ? "done" : s.n == $0 ? "current" : "todo" } ?? "todo"
                HStack(spacing: 8) {
                    Image(systemName: state == "done" ? "checkmark.circle.fill" : state == "current" ? "largecircle.fill.circle" : "circle")
                        .foregroundStyle(state == "todo" ? Color.secondary : state == "current" ? Color.red : Color.indigo)
                        .accessibilityHidden(true)
                    Text(s.name)
                        .font(state == "current" ? .subheadline.weight(.semibold) : .subheadline)
                        .foregroundStyle(state == "todo" ? .secondary : .primary)
                }
                .accessibilityElement(children: .combine)
                .accessibilityValue(state == "done" ? "пройден" : state == "current" ? "текущий этап" : "впереди")
            }
        }
    }
}

/// Схема и этапы: где мошенник сейчас и что сделает дальше.
struct SchemeProgressCard: View {
    var state: CallState
    var stages: [StageInfo]

    var body: some View {
        if let scheme = state.scheme {
            let current = state.currentStageN
            Card(
                scheme.schemeName, systemImage: "scope", tint: .indigo,
                trailing: AnyView(
                    NavigationLink(value: Route.scheme(scheme.schemeID)) { Text("Карточка").font(.subheadline) }
                )
            ) {
                HStack {
                    ConfidenceBar(value: scheme.confidence)
                    Text("уверенность \(scheme.percent)%").font(.caption).foregroundStyle(.secondary)
                }
                Stepper(stages: stages, current: current)
                if let stage = state.stage, current != nil, !stage.nextMove.isEmpty {
                    HStack(alignment: .firstTextBaseline) {
                        Text("Дальше").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                        Text(stage.nextMove).font(.subheadline)
                    }
                }
            }
        } else {
            Card("Схема", systemImage: "scope", tint: .indigo) {
                Text("Не определена. Агент сравнивает разговор с 32 известными схемами.").foregroundStyle(.secondary)
            }
        }
    }
}

/// Лента действий агента: инструменты, правила, разбор моделью — с таймингами.
struct AgentLogCard: View {
    var log: [AgentLogEntry]
    var replay: Bool
    @State var open = false

    var body: some View {
        let rows = log.compactMap(Self.row)
        Card {
            Button {
                withAnimation { open.toggle() }
            } label: {
                HStack {
                    Image(systemName: "list.bullet.rectangle").accessibilityHidden(true)
                    Text("Действия агента").font(.headline)
                    Chip("\(rows.count)")
                    Spacer()
                    Image(systemName: "chevron.down").rotationEffect(.degrees(open ? 180 : 0)).accessibilityHidden(true)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityValue(open ? "развёрнуто" : "свёрнуто")
            if !open, let last = rows.last {
                Text(last.text).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            if open {
                if replay {
                    Text("Это запись: паузы длиннее 15 с сокращены, при ускорении — все. Настоящие задержки агента видны в живом звонке.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Image(systemName: row.icon).foregroundStyle(row.tone.color).imageScale(.small).accessibilityHidden(true)
                        (Text(row.text) + Text(row.extra.map { " " + $0 } ?? "").foregroundColor(.secondary))
                            .font(.subheadline)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text(Format.clock(row.at)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    struct Row {
        var icon: String
        var text: String
        var extra: String?
        var tone: Tone
        var at: Double
    }

    static func row(_ e: AgentLogEntry) -> Row? {
        let times = (one: "раз", few: "раза", many: "раз")
        switch e {
        case .tool(let at, let tool, let ok, let ms, _, let result):
            if !ok { return Row(icon: "cylinder", text: "Инструмент \(tool) не ответил, звонок продолжается", tone: .neutral, at: at) }
            switch tool {
            case "lookup_number":
                let n = Int(result["calls_count"]?.doubleValue ?? 0)
                let text = n > 0 ? "Проверил номер: звонил \(Format.count(n, times))" : "Проверил номер: раньше не звонил"
                return Row(icon: "cylinder", text: text, extra: Format.ms(ms), tone: .neutral, at: at)
            case "get_scheme":
                return Row(icon: "cylinder", text: "Открыл карточку схемы", extra: Format.ms(ms), tone: .neutral, at: at)
            case "save_case":
                return Row(icon: "cylinder", text: "Сохранил кейс в базу", extra: Format.ms(ms), tone: .neutral, at: at)
            default:
                return Row(icon: "cylinder", text: tool, extra: Format.ms(ms), tone: .neutral, at: at)
            }
        case .warning(let at, let w):
            let lag = w.lag.map { "через \(Format.seconds($0)) после фразы" }
            switch w.kind {
            case .rule: return Row(icon: "exclamationmark.triangle", text: "Правило: \(w.labels.joined(separator: ", "))", extra: lag, tone: .red, at: at)
            case .demand: return Row(icon: "exclamationmark.triangle", text: "Модель: новое требование", extra: lag, tone: .red, at: at)
            case .history:
                return Row(icon: "exclamationmark.triangle", text: "Номер уже звонил \(Format.count(w.count ?? 0, times))", tone: .red, at: at)
            case .other: return Row(icon: "exclamationmark.triangle", text: w.text, tone: .red, at: at)
            }
        case .scheme(let at, let s):
            return Row(
                icon: "scope", text: "Определил схему \(s.schemeName), \(s.percent)%",
                extra: s.sinceAlarm.map { "через \(Format.seconds($0)) после первого признака" }, tone: .agent, at: at
            )
        case .stage(let at, let p):
            return Row(icon: "list.number", text: "Этап \(p.stageN): \(p.stageName)", tone: .neutral, at: at)
        case .suggestion(let at, let p):
            if p.kind == .hangup { return Row(icon: "phone.down", text: "Подсказал выход: положить трубку", tone: .neutral, at: at) }
            let tactic = Format.tactic(p.tactic).map { ", \($0)" } ?? ""
            return Row(icon: "text.bubble", text: "Подсказка: \(p.kind.title)\(tactic)", tone: .agent, at: at)
        case .summary(let at):
            return Row(icon: "doc.text", text: "Написал отчёт", tone: .neutral, at: at)
        case .ended(let at):
            return Row(icon: "phone.down", text: "Звонок закончен", tone: .neutral, at: at)
        }
    }
}

/// Агент упал: причина человеческими словами и одна команда.
struct CrashCard: View {
    var error: String
    var started: Bool
    var onReplayDemo: (() -> Void)?

    var body: some View {
        let d = Diagnose.explain(error)
        Card(started ? "Агент упал посреди звонка" : "Агент не запустился", systemImage: "exclamationmark.octagon", tint: .red) {
            Text(d.reason)
            if let fix = d.fix {
                Text("Что сделать на компьютере").font(.caption).foregroundStyle(.secondary)
                FixCommand(command: fix)
            }
            if let onReplayDemo {
                Text("Отчёт не сохранён. Пока модель не готова, показать работу интерфейса можно на записи — она идёт без модели.")
                    .font(.subheadline).foregroundStyle(.secondary)
                Button("Запустить демо-запись", systemImage: "play.fill", action: onReplayDemo).buttonStyle(.borderedProminent)
            }
            if !error.isEmpty {
                DisclosureGroup("Текст ошибки") {
                    Text(error).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .font(.subheadline)
            }
        }
    }
}
