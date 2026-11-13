import Foundation
import Testing
@testable import AntifraudCore

/// Мок-звонки из data/mock_events/ читаются прямо из репозитория.
enum Mocks {
    static let root = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()  // AntifraudCoreTests
        .deletingLastPathComponent()  // Tests
        .deletingLastPathComponent()  // AntifraudKit
        .deletingLastPathComponent()  // ios
        .deletingLastPathComponent()  // корень репозитория

    static let names = ["example", "fraud_bank_call", "fraud_relative", "no_fraud"]

    static func text(_ name: String) throws -> String {
        try String(contentsOf: root.appendingPathComponent("data/mock_events/\(name).jsonl"), encoding: .utf8)
    }

    static func events(_ name: String) throws -> [AgentEvent] {
        try AgentEvent.decodeLines(text(name))
    }

    static func state(_ name: String) throws -> CallState {
        CallState(events: try events(name))
    }
}

@Suite("Разбор мок-звонков")
struct MockParsingTests {
    @Test("Каждый файл разбирается целиком, без неизвестных событий", arguments: Mocks.names)
    func parsesWholeFile(name: String) throws {
        let text = try Mocks.text(name)
        let lines = text.split(whereSeparator: \.isNewline).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        let events = try Mocks.events(name)
        #expect(events.count == lines.count)
        let unknown = events.filter { if case .unknown = $0.payload { true } else { false } }
        #expect(unknown.isEmpty, "не разобрались: \(unknown.map(\.type))")
        #expect(events.first?.type == "call_started")
        #expect(events.last?.type == "call_ended")
    }

    @Test("Число событий в файлах")
    func counts() throws {
        #expect(try Mocks.events("example").count == 27)
        #expect(try Mocks.events("fraud_bank_call").count == 38)
        #expect(try Mocks.events("fraud_relative").count == 33)
        #expect(try Mocks.events("no_fraud").count == 14)
    }

    @Test("Старый формат: без seq")
    func oldFormatHasNoSeq() throws {
        #expect(try Mocks.events("example").allSatisfy { $0.seq == nil })
        #expect(try Mocks.events("fraud_relative").map(\.seq) == Array(0..<33))
    }

    @Test("Даты с долями секунды, без них и без зоны")
    func dates() throws {
        let events = try Mocks.events("fraud_relative")
        #expect(ISODate.format(events[0].ts) == "2026-10-29T19:33:00.955Z")
        let state = CallState(events: events)
        // first_seen в результате lookup_number записан без зоны.
        #expect(state.numberInfo?.firstSeen == ISODate.parse("2026-08-09T08:37:00Z"))
    }
}

@Suite("CallState на мок-звонках — как callView.js")
struct CallStateMockTests {
    @Test func fraudRelative() throws {
        let v = try Mocks.state("fraud_relative")
        #expect(v.callID == "mock-fraud_relative")
        #expect(v.phone == "+79990000002")
        #expect(v.source == AudioSourceInfo(kind: "file", path: "data/calls/002.txt"))
        #expect(v.verdict == .scheme)
        #expect(v.scheme?.schemeID == "relative-accident")
        #expect(v.scheme?.percent == 95)
        #expect(v.stages.map(\.n) == [1, 2, 3, 4])
        #expect(v.stage?.stageN == 2)
        #expect(v.currentStageN == 2)
        #expect(v.maxStage == 2)
        #expect(v.lines.count == 12)
        #expect(v.warnings.map(\.kind) == [.history, .rule, .rule, .demand, .demand])
        #expect(v.alarms.count == 4)
        #expect(v.warnings[0].count == 2)
        #expect(v.warnings[0].schemes == ["relative-accident"])
        #expect(v.warnings[1].labels == ["наличные / банкомат / курьер"])
        #expect(v.warnings.map(\.lineSeq) == [nil, 8, 9, 7, 11])
        #expect(v.warnings[3].hits.map(\.seq) == [5, 7])
        #expect(v.warnings[4].hits.map(\.seq) == [7, 8, 9, 11])
        #expect(v.hints.count == 2)
        #expect(v.lastHint?.tactic == "fake_compliance")
        #expect(v.hangup?.text == "Положите трубку и перезвоните родственнику на его обычный номер.")
        #expect(v.numberInfo?.callsCount == 2)
        #expect(v.summary?.schemeID == "relative-accident")
        #expect(v.summary?.maxStage == 2)
        #expect(v.summaryFromOtherCall == false)
        #expect(v.ended)
        #expect(v.schemeCard?.stages?.first?.goal == "вызвать панику и не дать узнать голос")
        #expect(abs((v.scheme?.sinceAlarm ?? 0) - 20.319) < 0.01)
        #expect(abs((v.scheme?.at ?? 0) - 57.522) < 0.01)
        #expect(v.inline.count == 1)
        #expect(v.inline[0].kind == .scheme && v.inline[0].afterSeq == 11 && v.inline[0].stage?.stageN == 2)
    }

    @Test func fraudRelativeMarks() throws {
        let v = try Mocks.state("fraud_relative")
        func marks(_ i: Int) -> [String] { v.lines[i].marks.map { "\($0.kind.rawValue) \($0.start)-\($0.end)" } }
        #expect(marks(4) == ["evidence 14-115"])
        #expect(marks(5) == ["evidence 0-65", "demand 0-65"])
        #expect(marks(7) == ["evidence 0-88", "demand 0-88", "demand 0-88"])
        #expect(marks(8) == ["rule 0-33", "demand 0-56"])
        #expect(marks(9) == ["rule 63-101", "demand 0-101"])
        #expect(marks(11) == ["demand 0-56"])
        func segs(_ i: Int) -> [String] { v.lines[i].segments.map { "\($0.kind?.rawValue ?? "-") \($0.text.count)" } }
        #expect(segs(8) == ["rule 33", "demand 23", "- 1"])
        #expect(segs(9) == ["demand 63", "rule 38", "- 1"])
        #expect(segs(4) == ["- 14", "evidence 101", "- 1"])
        #expect(v.lines[8].flagLabels == ["наличные / банкомат / курьер", "требование"])
    }

    @Test func fraudBankCall() throws {
        let v = try Mocks.state("fraud_bank_call")
        #expect(v.verdict == .scheme)
        #expect(v.scheme?.schemeID == "gosuslugi")
        #expect(v.scheme?.percent == 90)
        #expect(v.stages.count == 3)
        #expect(v.stage?.stageN == 3)
        #expect(v.maxStage == 3)
        #expect(v.warnings.map(\.kind) == [.history, .rule, .demand, .rule, .rule, .rule, .demand])
        #expect(v.warnings.map(\.lineSeq) == [nil, 5, 4, 7, 8, 9, nil])
        #expect(v.warnings[1].labels == ["перевод денег", "безопасный счёт"])
        #expect(v.warnings[0].count == 7)
        #expect(v.warnings[0].schemes == ["safe-account", "pension-recalculation", "law-enforcement"])
        #expect(v.alarms.count == 6)
        #expect(v.suggestions.count == 7)
        #expect(v.hints.count == 4)
        #expect(v.hints.filter { $0.kind == .provoke }.count == 2)
        #expect(v.hangup?.text.hasPrefix("Положите трубку. Зайдите в Госуслуги сами") == true)
        #expect(v.numberInfo?.callsCount == 7)
        #expect(v.summary?.schemeID == "gosuslugi")
        #expect(v.summary?.maxStage == 3)
        #expect(v.inline.map(\.afterSeq) == [6, 11])
        #expect(v.inline.map(\.kind) == [.scheme, .stage])
        #expect(abs((v.scheme?.sinceAlarm ?? 0) - 6.42) < 0.01)
        let logKinds = v.log.map { e -> String in
            switch e {
            case .tool: "tool"
            case .warning: "warning"
            case .scheme: "scheme"
            case .stage: "stage"
            case .suggestion: "suggestion"
            case .summary: "summary"
            case .ended: "ended"
            }
        }
        #expect(logKinds.joined(separator: ",") == "tool,warning,warning,suggestion,tool,scheme,stage,warning,suggestion,warning,suggestion,warning,warning,suggestion,stage,suggestion,warning,suggestion,suggestion,summary,tool,ended")
        // Время работы инструмента — по паре tool_call / tool_result.
        if case .tool(_, let tool, let ok, let ms, let args, _) = v.log[0] {
            #expect(tool == "lookup_number" && ok)
            #expect(ms != nil)
            #expect(args?["phone"] == .string("+79990000001"))
        } else {
            Issue.record("первым в ленте должен быть lookup_number")
        }
    }

    @Test func noFraud() throws {
        let v = try Mocks.state("no_fraud")
        #expect(v.verdict == .none)
        #expect(v.scheme == nil)
        #expect(v.warnings.isEmpty)
        #expect(v.suggestions.isEmpty)
        #expect(v.stages.isEmpty)
        #expect(v.lines.count == 7)
        #expect(v.numberInfo?.callsCount == 0)
        #expect(v.summary != nil)
        #expect(v.summary?.schemeID == nil)
        #expect(v.summary?.maxStage == nil)
        #expect(v.ended)
    }

    @Test("Эталон старого формата: этапов нет ни в событии, ни в карточке")
    func example() throws {
        let v = try Mocks.state("example")
        #expect(v.verdict == .scheme)
        #expect(v.scheme?.schemeID == "safe-account")
        #expect(v.schemeCard?.id == "safe-account")
        #expect(v.stages.isEmpty)
        #expect(v.stage?.stageN == 3)
        #expect(v.maxStage == 3)
        // «Номер уже звонил…» — не формат истории из agent/rules.py, поэтому это плашка other.
        #expect(v.warnings.map(\.kind) == [.other, .other, .other])
        #expect(v.alarms.count == 3)
        #expect(v.hints.count == 3)
        #expect(v.hangup == nil)
        #expect(v.summary?.maxStage == 3)
        #expect(v.inline.map(\.afterSeq) == [4, 6, 7])
        #expect(v.inline.map { $0.stage?.stageN } == [1, 2, 3])
        #expect(v.lines[0].marks.map { "\($0.start)-\($0.end)" } == ["27-76"])
        #expect(v.lines[4].marks.map { "\($0.start)-\($0.end)" } == ["45-95"])
        #expect(abs((v.scheme?.sinceAlarm ?? 0) - 22.6) < 0.01)
    }

    @Test("Неизвестный тип события не ломает разбор")
    func unknownEvent() throws {
        var lines = try Mocks.text("fraud_relative").split(separator: "\n").map(String.init)
        lines.insert(#"{"call_id":"mock-fraud_relative","seq":99,"ts":"2026-10-29T19:33:10Z","type":"emotion","payload":{"level":0.7}}"#, at: 6)
        // Известный тип с payload не той формы — тоже не ошибка.
        lines.insert(#"{"call_id":"mock-fraud_relative","ts":"2026-10-29T19:33:11Z","type":"warning","payload":{"txt":1}}"#, at: 7)
        let events = try AgentEvent.decodeLines(lines.joined(separator: "\n"))
        #expect(events.count == 35)
        guard case .unknown(let type, let payload) = events[6].payload else {
            Issue.record("ожидалось unknown")
            return
        }
        #expect(type == "emotion")
        #expect(payload["level"]?.doubleValue == 0.7)
        let v = CallState(events: events)
        #expect(v.unknownTypes == ["emotion", "warning"])
        #expect(v.scheme?.schemeID == "relative-accident")
        #expect(v.warnings.count == 5)
        #expect(v.ended)
    }

    @Test("Пошаговое применение даёт то же, что разбор целиком")
    func incremental() throws {
        let events = try Mocks.events("fraud_bank_call")
        var v = CallState()
        for (i, e) in events.enumerated() {
            v.apply(e)
            if i == 10 { #expect(!v.ended) }
        }
        let whole = CallState(events: events)
        #expect(v.lines == whole.lines)
        #expect(v.warnings == whole.warnings)
        #expect(v.eventCount == events.count)
    }
}
