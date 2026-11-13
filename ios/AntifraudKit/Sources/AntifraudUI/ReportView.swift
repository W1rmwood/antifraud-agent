import AntifraudCore
import SwiftUI

/// Отчёт о звонке — перенос ui/web/src/screens/Report.jsx.
/// state — звонок из лога событий; если лога нет, показывается то, что агент сохранил в базу (callCase).
struct ReportView: View {
    @Environment(AppModel.self) private var model
    var state: CallState?
    var callCase: CallCase
    var stages: [StageInfo] = []
    var schemeName: String?
    var replay = false

    @State private var section = 0
    @State private var loaded: Scheme?
    @State private var copied = false

    private var c: CallCase { callCase }
    private var fraud: Bool { c.schemeID != nil }

    /// Карточка схемы из лога (если в ней есть цели этапов) или загруженная с сервера.
    private var cardStages: [SchemeCard.CardStage] {
        if let card = state?.schemeCard, card.id == c.schemeID, card.stages?.first?.goal != nil { return card.stages ?? [] }
        if let loaded {
            return loaded.stages.map { .init(n: $0.n, name: $0.name, goal: $0.goal, asksFor: $0.asksFor, nextMove: $0.nextMove) }
        }
        return stages.map { .init(n: $0.n, name: $0.name, goal: nil, asksFor: nil, nextMove: nil) }
    }

    private var title: String {
        state?.scheme?.schemeName ?? loaded?.name ?? schemeName ?? (fraud ? c.schemeID ?? "" : "Обычный звонок")
    }

    private var confidence: Int? { state?.scheme?.percent }
    private var reached: Int { c.maxStage ?? 0 }
    private var demands: [ReportBuilder.Demand] { ReportBuilder.demands(state?.alarms ?? []) }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            head
            if state != nil {
                Picker("Раздел", selection: $section) {
                    Text("Итог").tag(0)
                    Text("Стенограмма").tag(1)
                    Text("Действия агента").tag(2)
                }
                .pickerStyle(.segmented)
            }
            switch section {
            case 1:
                if let state {
                    if state.lines.isEmpty { Text("В этом звонке не было ни одной фразы.").foregroundStyle(.secondary) }
                    LazyVStack(alignment: .leading, spacing: 6) { TranscriptItems(lines: state.lines, notes: state.inline) }
                }
            case 2:
                if let state { AgentLogCard(log: state.log, replay: replay, open: true) }
            default:
                summarySection
            }
        }
        .task(id: c.schemeID) { await loadScheme() }
    }

    // MARK: - Шапка и свойства

    private var head: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Chip(fraud ? "Мошенничество" : "Мошенничества не найдено", tone: fraud ? .red : .green)
                if replay { Chip("запись") }
            }
            Text(title).font(.title2.weight(.bold))
            if fraud, let legend = c.legend, !legend.isEmpty, legend != title {
                Text("Представлялись: \(legend)").foregroundStyle(.secondary)
            }
        }
    }

    private var props: some View {
        Card {
            VStack(spacing: 8) {
                PropRow(label: "Номер") {
                    VStack(alignment: .trailing) {
                        if let phone = c.phone {
                            NavigationLink(value: Route.number(phone)) { Text(Format.phone(phone) ?? phone) }
                        } else {
                            Text("неизвестен")
                        }
                        let n = state?.numberInfo?.callsCount ?? 0
                        if n > 0 {
                            Text("звонил уже \(Format.count(n, (one: "раз", few: "раза", many: "раз")))").font(.caption).foregroundStyle(.red)
                        }
                    }
                }
                PropRow(label: "Начало") { Text(Format.when(c.startedAt)) }
                if c.endedAt != nil {
                    PropRow(label: "Длительность") { Text(Format.duration(c.startedAt, c.endedAt)).monospacedDigit() }
                }
                if let source = Format.source(state?.source, replay: replay) { PropRow(label: "Источник") { Text(source) } }
                if fraud, let id = c.schemeID {
                    PropRow(label: "Схема") {
                        VStack(alignment: .trailing) {
                            NavigationLink(value: Route.scheme(id)) { Text(title) }
                            if let confidence { Text("уверенность \(confidence)%").font(.caption).foregroundStyle(.secondary) }
                        }
                    }
                }
                if fraud, reached > 0 {
                    PropRow(label: "Этап") { Text("\(reached) из \(cardStages.isEmpty ? "?" : String(cardStages.count))") }
                }
                if let state {
                    PropRow(label: "Признаков") {
                        VStack(alignment: .trailing) {
                            Text("\(state.alarms.count)")
                            if let first = state.alarms.first {
                                Text("первый в \(Format.clock(first.phraseAt ?? first.at))").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    if let since = state.scheme?.sinceAlarm {
                        PropRow(label: "Агент понял") {
                            VStack(alignment: .trailing) {
                                Text("через \(Format.seconds(since))")
                                Text("после первого признака\(replay ? ", в записи паузы сжаты" : "")").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    PropRow(label: "Подсказок") { Text("\(state.hints.count)") }
                }
                PropRow(label: "Кейс") { Text(caseStatus) }
            }
            .font(.subheadline)
            HStack {
                Button(copied ? "Скопировано" : "Скопировать отчёт", systemImage: "doc.on.doc") {
                    Platform.copy(reportText)
                    copied = true
                    Task {
                        try? await Task.sleep(for: .seconds(1.6))
                        copied = false
                    }
                }
                .buttonStyle(.bordered)
                ShareLink(item: reportText) { Label("Поделиться", systemImage: "square.and.arrow.up") }
                    .buttonStyle(.bordered)
            }
            Text("Текстом: вывод, требования мошенника и что делать — чтобы переслать близким.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var caseStatus: String {
        if replay { return "запись — в базу не пишется" }
        guard let state else { return "в базе" }
        let saved = state.log.contains { if case .tool(_, "save_case", true, _, _, _) = $0 { true } else { false } }
        return saved ? "сохранён в базу" : "не сохранён"
    }

    private var reportText: String {
        ReportBuilder.text(
            callCase: c, title: title, stages: cardStages.map { StageInfo(n: $0.n, name: $0.name) }, demands: demands,
            hangup: state?.hangup?.text, confidence: confidence
        )
    }

    // MARK: - Итог

    @ViewBuilder
    private var summarySection: some View {
        VStack(alignment: .leading, spacing: 18) {
            section("Кратко") {
                Text(c.summary.isEmpty ? "Агент не написал итог." : c.summary)
            }
            if fraud, !cardStages.isEmpty {
                section("Сценарий мошенника") {
                    Text(
                        (reached > 0 ? "Мошенник дошёл до этапа \(reached) из \(cardStages.count)." : "До какого этапа дошёл мошенник, агент не определил.")
                            + (reached > 0 && reached < cardStages.count ? " Ниже — что было бы дальше, если бы разговор продолжился." : "")
                    )
                    .font(.subheadline).foregroundStyle(.secondary)
                    ForEach(cardStages, id: \.n) { s in scenarioStep(s) }
                }
            }
            if !demands.isEmpty {
                section("Что требовал мошенник") {
                    ForEach(Array(demands.enumerated()), id: \.offset) { _, d in
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Text(Format.clock(d.at)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("«\(d.quote.trimmingCharacters(in: CharacterSet(charactersIn: ".…")))»")
                                Text(d.labels.joined(separator: ", ")).font(.caption).foregroundStyle(.red)
                            }
                        }
                    }
                }
            }
            if let state, !state.hints.isEmpty {
                section("Что подсказывал агент") {
                    ForEach(Array(state.hints.enumerated()), id: \.offset) { _, h in
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Text(Format.clock(h.at)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(h.text)
                                Chip(Format.suggestionLabel(kind: h.kind, tactic: h.tactic), tone: .agent)
                            }
                        }
                    }
                }
            }
            if !c.keyPhrases.isEmpty {
                section("Ключевые фразы") { Chips(items: c.keyPhrases) }
            }
            props
        }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            content()
        }
    }

    private func scenarioStep(_ s: SchemeCard.CardStage) -> some View {
        let state = s.n < reached ? "done" : s.n == reached ? "current" : "todo"
        let label = ["done": "пройден", "current": "здесь остановился", "todo": "не дошёл"][state] ?? ""
        return HStack(alignment: .top, spacing: 10) {
            Image(systemName: state == "done" ? "checkmark.circle.fill" : state == "current" ? "largecircle.fill.circle" : "circle")
                .foregroundStyle(state == "todo" ? Color.secondary : state == "current" ? Color.red : Color.indigo)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(s.name).font(.subheadline.weight(.semibold))
                    Spacer()
                    Text(label).font(.caption).foregroundStyle(.secondary)
                }
                if let goal = s.goal { Text("Цель: \(goal)").font(.caption).foregroundStyle(.secondary) }
                if let asks = s.asksFor, !asks.isEmpty, state != "done" {
                    Text(state == "current" ? "Просил" : "Попросил бы").font(.caption).foregroundStyle(.secondary)
                    Chips(items: asks, tone: state == "current" ? .red : .neutral)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func loadScheme() async {
        guard let id = c.schemeID, loaded?.id != id else { return }
        if let card = state?.schemeCard, card.id == id, card.stages?.first?.goal != nil { return }
        loaded = try? await model.client?.scheme(id)
    }
}
