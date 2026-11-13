import Foundation

// Состояние экрана звонка из событий агента — перенос ui/web/src/callView.js.
// В вебе состояние пересчитывается по всему списку событий; здесь оно копится событие за событием
// (apply), а результат тот же: живой звонок, запись и открытый из истории звонок рисуются одним кодом.

public enum Verdict: String, Sendable, Hashable {
    /// Схема определена.
    case scheme
    /// Есть тревожные признаки (или номер уже звонил).
    case warning
    /// Ничего.
    case none
}

/// Пометка на реплике: где сработало правило, требование модели или основание схемы.
public struct LineMark: Sendable, Hashable {
    public enum Kind: String, Sendable, Hashable {
        case rule, demand, other, evidence

        /// Красная пометка важнее основания схемы (segments в callView.js).
        var rank: Int {
            switch self {
            case .rule, .demand: 3
            case .other: 2
            case .evidence: 1
            }
        }
    }

    /// Границы в символах (Character) текста реплики.
    public var start: Int
    public var end: Int
    public var kind: Kind
    public var labels: [String]
}

/// Реплика в ленте.
public struct TranscriptLine: Sendable, Hashable, Identifiable {
    public var seq: Int
    public var speaker: Speaker
    public var text: String
    public var tStart: Double
    public var tEnd: Double
    /// Когда реплика пришла (время события).
    public var ts: Date
    /// Время разговора в момент прихода, секунды.
    public var at: Double
    public var marks: [LineMark]

    public var id: Int { seq }

    /// Есть ли красная пометка (не только основание схемы).
    public var flagged: Bool { marks.contains { $0.kind != .evidence } }

    /// Метки признаков под репликой, без повторов.
    public var flagLabels: [String] {
        var seen = Set<String>()
        return marks.filter { $0.kind != .evidence }.flatMap(\.labels).filter { seen.insert($0).inserted }
    }

    /// Отрезки текста для подсветки.
    public var segments: [TextSegment] { CallState.segments(text, marks) }
}

public struct TextSegment: Sendable, Hashable {
    public var text: String
    public var kind: LineMark.Kind?
}

/// Плашка-предупреждение, разобранная по тексту (parseWarning) и привязанная к репликам.
public struct WarningItem: Sendable, Hashable {
    public enum Kind: String, Sendable, Hashable {
        /// Правило без модели (agent/rules.py): «Признак развода: …».
        case rule
        /// Требование, найденное моделью: «Мошенник требует: …».
        case demand
        /// История номера: «С этого номера уже звонили N раз…».
        case history
        case other
    }

    public struct Hit: Sendable, Hashable {
        public var quote: String
        /// Реплика, где сказана цитата; nil — не нашлась.
        public var seq: Int?
        public var at: Double
    }

    public var kind: Kind
    public var labels: [String]
    public var quotes: [String]
    public var text: String
    /// Для history: сколько раз звонили и какие схемы.
    public var count: Int?
    public var schemes: [String]
    public var ts: Date
    public var at: Double
    public var hits: [Hit]
    /// Сколько секунд прошло от последней процитированной фразы до плашки.
    public var lag: Double?
    public var lineSeq: Int?
    public var phraseAt: Double?

    /// Заголовок в списке признаков, как Signs в AgentPanel.jsx.
    public var title: String {
        switch kind {
        case .rule: labels.joined(separator: ", ")
        case .demand: "Требование"
        case .other: "Предупреждение"
        case .history: "Номер уже звонил"
        }
    }
}

/// Объявленная схема и когда это случилось.
public struct DetectedScheme: Sendable, Hashable {
    public var payload: SchemeDetectedPayload
    public var ts: Date
    public var at: Double
    /// Через сколько секунд после первого признака агент понял схему.
    public var sinceAlarm: Double?

    public var schemeID: String { payload.schemeID }
    public var schemeName: String { payload.schemeName }
    public var confidence: Double { payload.confidence }
    /// Уверенность в процентах.
    public var percent: Int { Int((payload.confidence * 100).rounded()) }
}

/// Подсказка агента с временем прихода.
public struct SuggestionItem: Sendable, Hashable {
    public var payload: SuggestionPayload
    public var ts: Date
    public var at: Double

    public var text: String { payload.text }
    public var kind: SuggestionKind { payload.kind }
    public var tactic: String? { payload.tactic }
}

/// Карточка схемы из результата get_scheme. В старых логах там только id — поля необязательные.
public struct SchemeCard: Decodable, Sendable, Hashable {
    public struct CardStage: Decodable, Sendable, Hashable {
        public var n: Int
        public var name: String
        public var goal: String?
        public var asksFor: [String]?
        public var nextMove: String?

        enum CodingKeys: String, CodingKey {
            case n, name, goal, asksFor = "asks_for", nextMove = "next_move"
        }

        public init(n: Int, name: String, goal: String? = nil, asksFor: [String]? = nil, nextMove: String? = nil) {
            self.n = n
            self.name = name
            self.goal = goal
            self.asksFor = asksFor
            self.nextMove = nextMove
        }
    }

    public var id: String
    public var name: String?
    public var legend: String?
    public var stages: [CardStage]?

    public var stageInfos: [StageInfo] { (stages ?? []).map { StageInfo(n: $0.n, name: $0.name) } }
}

/// Строка ленты действий агента.
public enum AgentLogEntry: Sendable, Hashable {
    case tool(at: Double, tool: String, ok: Bool, ms: Double?, args: [String: JSONValue]?, result: JSONValue)
    case warning(at: Double, WarningItem)
    case scheme(at: Double, DetectedScheme)
    case stage(at: Double, StageChangedPayload)
    case suggestion(at: Double, SuggestionPayload)
    case summary(at: Double)
    case ended(at: Double)

    public var at: Double {
        switch self {
        case .tool(let at, _, _, _, _, _), .warning(let at, _), .scheme(let at, _), .stage(let at, _),
             .suggestion(let at, _), .summary(let at), .ended(let at):
            at
        }
    }
}

/// Событие агента между репликами: где в разговоре он понял схему или сменился этап.
public struct InlineNote: Sendable, Hashable {
    public enum Kind: Sendable, Hashable { case scheme, stage }
    public var kind: Kind
    /// После какой реплики показывать; -1 — до первой.
    public var afterSeq: Int
    public var at: Double
    public var scheme: DetectedScheme?
    public var stage: StageChangedPayload?
}

public struct CallState: Sendable {
    public private(set) var callID: String?
    public private(set) var phone: String?
    public private(set) var source: AudioSourceInfo?
    public private(set) var startedTs: Date?
    public private(set) var endedTs: Date?
    public private(set) var lastTs: Date?
    public private(set) var lines: [TranscriptLine] = []
    /// Последняя объявленная схема.
    public private(set) var scheme: DetectedScheme?
    /// Схема целиком из get_scheme.
    public private(set) var schemeCard: SchemeCard?
    public private(set) var stage: StageChangedPayload?
    public private(set) var maxStage = 0
    public private(set) var warnings: [WarningItem] = []
    public private(set) var suggestions: [SuggestionItem] = []
    public private(set) var log: [AgentLogEntry] = []
    public private(set) var inline: [InlineNote] = []
    public private(set) var numberInfo: NumberInfo?
    public private(set) var summary: CallCase?
    public private(set) var ended = false
    /// Сколько событий применено.
    public private(set) var eventCount = 0
    /// Типы событий, которых приложение не знает: видны в отладке, на экран не влияют.
    public private(set) var unknownTypes: [String] = []

    private var stagesFromEvent: [StageInfo] = []
    private var openTools: [(tool: String, args: [String: JSONValue], ts: Date)] = []

    public init() {}

    public init(events: some Sequence<AgentEvent>) {
        for e in events { apply(e) }
    }

    // MARK: - Производные поля (в вебе считаются после цикла)

    /// Этапы схемы. В логе до 30.10 этапов в scheme_detected нет — берём их из карточки схемы.
    public var stages: [StageInfo] {
        if !stagesFromEvent.isEmpty { return stagesFromEvent }
        if let scheme, let card = schemeCard, card.id == scheme.schemeID { return card.stageInfos }
        return []
    }

    public var verdict: Verdict {
        scheme != nil ? .scheme : warnings.isEmpty ? .none : .warning
    }

    /// Последний «положите трубку».
    public var hangup: SuggestionItem? { suggestions.last { $0.kind == .hangup } }

    /// Подсказки модели (без hangup).
    public var hints: [SuggestionItem] { suggestions.filter { $0.kind != .hangup } }

    /// Последняя подсказка модели.
    public var lastHint: SuggestionItem? { hints.last }

    /// Плашки-тревоги (без истории номера).
    public var alarms: [WarningItem] { warnings.filter { $0.kind != .history } }

    /// Номер текущего этапа, если он относится к объявленной схеме.
    public var currentStageN: Int? {
        guard let scheme, let stage, stage.schemeID == scheme.schemeID else { return nil }
        return stage.stageN
    }

    /// Отчёт — от другого звонка: это проигранная запись (проигрыватель переписывает call_id событий, но не отчёта).
    public var summaryFromOtherCall: Bool {
        guard let summary else { return false }
        return summary.callID != callID
    }

    /// Время разговора в момент ts, секунды. Считается от последней реплики (её tEnd — время в записи),
    /// а не от первого события: при ускоренном проигрывании настенные часы идут быстрее разговора.
    public func clock(at ts: Date) -> Double {
        guard let startedTs else { return 0 }
        guard let last = lines.last else { return max(0, ts.timeIntervalSince(startedTs)) }
        return last.tEnd + max(0, ts.timeIntervalSince(last.ts))
    }

    // MARK: - Применение события

    public mutating func apply(_ e: AgentEvent) {
        let ts = e.ts
        callID = e.callID
        if startedTs == nil { startedTs = ts }
        lastTs = ts
        eventCount += 1
        let at = clock(at: ts)
        let lastSeq = lines.last?.seq ?? -1

        switch e.payload {
        case .callStarted(let p):
            phone = p.phone
            source = p.source

        case .transcript(let p):
            lines.append(TranscriptLine(
                seq: p.seq, speaker: p.speaker, text: p.text, tStart: p.tStart, tEnd: p.tEnd, ts: ts, at: at, marks: []
            ))

        case .warning(let p):
            var w = Self.parseWarning(p.text, ts: ts, at: at)
            for quote in w.quotes {
                let hit = Self.findQuote(lines, quote)
                w.hits.append(.init(quote: quote, seq: hit.map { lines[$0.line].seq }, at: hit.map { lines[$0.line].tStart } ?? at))
                guard let hit else { continue }
                lines[hit.line].marks.append(LineMark(start: hit.start, end: hit.end, kind: w.markKind, labels: w.labels))
                let lag = max(0, ts.timeIntervalSince(lines[hit.line].ts))
                if w.lag == nil || lag < w.lag! {
                    w.lag = lag
                    w.lineSeq = lines[hit.line].seq
                    w.phraseAt = lines[hit.line].tStart
                }
            }
            warnings.append(w)
            log.append(.warning(at: at, w))

        case .schemeDetected(let p):
            let firstAlarm = warnings.first { $0.kind != .history }
            let detected = DetectedScheme(payload: p, ts: ts, at: at, sinceAlarm: firstAlarm.map { at - $0.at })
            scheme = detected
            if !p.stages.isEmpty {
                stagesFromEvent = p.stages
            } else if let card = schemeCard, card.id == p.schemeID {
                stagesFromEvent = card.stageInfos
            } else {
                stagesFromEvent = []
            }
            stage = nil
            for quote in p.evidence {
                if let hit = Self.findQuote(lines, quote) {
                    lines[hit.line].marks.append(LineMark(start: hit.start, end: hit.end, kind: .evidence, labels: []))
                }
            }
            log.append(.scheme(at: at, detected))
            inline.append(InlineNote(kind: .scheme, afterSeq: lastSeq, at: at, scheme: detected, stage: nil))

        case .stageChanged(let p):
            stage = p
            maxStage = max(maxStage, p.stageN)
            log.append(.stage(at: at, p))
            // Этап сразу после объявления схемы уже виден в её строке — отдельной не нужно.
            if let last = inline.last, last.kind == .scheme, last.afterSeq == lastSeq {
                inline[inline.count - 1].stage = p
            } else {
                inline.append(InlineNote(kind: .stage, afterSeq: lastSeq, at: at, scheme: nil, stage: p))
            }

        case .suggestion(let p):
            suggestions.append(SuggestionItem(payload: p, ts: ts, at: at))
            log.append(.suggestion(at: at, p))

        case .toolCall(let p):
            openTools.append((p.tool, p.args, ts))

        case .toolResult(let p):
            let call = openTools.firstIndex { $0.tool == p.tool }.map { openTools.remove(at: $0) }
            log.append(.tool(
                at: at, tool: p.tool, ok: p.ok, ms: call.map { ts.timeIntervalSince($0.ts) * 1000 }, args: call?.args,
                result: p.result
            ))
            if p.tool == "lookup_number", p.ok { numberInfo = try? p.result.decode(NumberInfo.self) }
            if p.tool == "get_scheme", p.ok, p.result != .null, let card = try? p.result.decode(SchemeCard.self) {
                schemeCard = card
            }

        case .summary(let p):
            summary = p
            log.append(.summary(at: at))

        case .callEnded:
            ended = true
            endedTs = ts
            log.append(.ended(at: at))

        case .unknown(let type, _):
            unknownTypes.append(type)
        }
    }

    // MARK: - Разбор плашек и цитат

    static let advice = "Ничего не называй и не переводи."

    /// Разбор текста плашки: правила (agent/rules.py), требования из разбора моделью, история номера.
    public static func parseWarning(_ text: String, ts: Date = Date(timeIntervalSince1970: 0), at: Double = 0) -> WarningItem {
        let quotes = text.matches(of: /«([^»]+)»/).map { String($0.output.1) }
        func item(_ kind: WarningItem.Kind, _ labels: [String], _ quotes: [String], _ text: String) -> WarningItem {
            WarningItem(
                kind: kind, labels: labels, quotes: quotes, text: text, count: nil, schemes: [], ts: ts, at: at, hits: []
            )
        }
        if let m = text.firstMatch(of: /^Признак развода: (.+?) — «/) {
            return item(.rule, m.output.1.components(separatedBy: ", "), quotes, text)
        }
        if text.hasPrefix("Мошенник требует:") {
            return item(.demand, ["требование"], quotes, text)
        }
        if let m = text.wholeMatch(of: /С этого номера уже звонили (\d+) раз(?:, схемы: (.+))?\./) {
            var w = item(.history, [], [], text)
            w.count = Int(m.output.1)
            w.schemes = m.output.2.map { $0.components(separatedBy: ", ") } ?? []
            return w
        }
        var cleaned = text
        if let r = cleaned.range(of: advice) { cleaned.removeSubrange(r) }
        return item(.other, [], quotes, cleaned.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    static func norm(_ s: String) -> String {
        s.lowercased().replacingOccurrences(of: "ё", with: "е")
    }

    // Как tokenize в rag/chunks.py: слово, обрезанное до 5 букв, — грубый стеммер для сверки цитат.
    static func stems(_ s: String) -> [String] {
        norm(s).matches(of: /[a-zа-я0-9]+/).map { String($0.output.prefix(5)) }
    }

    /// Как в agent/policy.py: цитата модели может быть пересказом.
    static let quoteOverlap = 0.7

    /// Где в разговоре сказана цитата: точное место в реплике или вся реплика, если модель пересказала.
    /// Границы — в символах текста реплики.
    public static func findQuote(_ lines: [TranscriptLine], _ quote: String) -> (line: Int, start: Int, end: Int)? {
        var q = norm(quote)
        while let last = q.last, last.isWhitespace || ".!?…,;:".contains(last) { q.removeLast() }
        q = q.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return nil }
        for i in lines.indices.reversed() where lines[i].speaker != .user {
            let said = norm(lines[i].text)
            if let r = said.range(of: q) {
                let start = said.distance(from: said.startIndex, to: r.lowerBound)
                return (i, start, start + q.count)
            }
        }
        let want = stems(quote)
        guard !want.isEmpty else { return nil }
        var best: (line: Int, score: Double)?
        for i in lines.indices where lines[i].speaker != .user {
            let said = Set(stems(lines[i].text))
            let score = Double(want.filter { said.contains($0) }.count) / Double(want.count)
            if score >= quoteOverlap, best == nil || score > best!.score { best = (i, score) }
        }
        return best.map { ($0.line, 0, lines[$0.line].text.count) }
    }

    /// Отрезки реплики для подсветки: пометки могут пересекаться, красная важнее основания схемы.
    public static func segments(_ text: String, _ marks: [LineMark]) -> [TextSegment] {
        guard !marks.isEmpty else { return [TextSegment(text: text, kind: nil)] }
        let chars = Array(text)
        let cuts = Set([0, chars.count] + marks.flatMap { [$0.start, $0.end] })
            .map { min(max($0, 0), chars.count) }
        let sorted = Array(Set(cuts)).sorted()
        var out: [TextSegment] = []
        for (a, b) in zip(sorted, sorted.dropFirst()) where a < b {
            let top = marks.filter { $0.start <= a && $0.end >= b }.max { $0.kind.rank < $1.kind.rank }
            let piece = String(chars[a..<b])
            if let last = out.last, last.kind == top?.kind {
                out[out.count - 1].text += piece
            } else {
                out.append(TextSegment(text: piece, kind: top?.kind))
            }
        }
        return out
    }
}

extension WarningItem {
    var markKind: LineMark.Kind {
        switch kind {
        case .rule: .rule
        case .demand: .demand
        case .other, .history: .other
        }
    }
}
