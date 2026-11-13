import Foundation
import Testing
@testable import AntifraudCore

@Suite("Отчёт: требования и текст")
struct ReportBuilderTests {
    @Test("Требования без повторов, с метками правил и модели — как Report.jsx")
    func demandsRelative() throws {
        let v = try Mocks.state("fraud_relative")
        let d = ReportBuilder.demands(v.alarms)
        #expect(d.map(\.seq) == [5, 7, 8, 9, 11])
        #expect(d[2].labels == ["наличные / банкомат / курьер", "требование"])
        #expect(d[3].labels == ["никому не говорить", "требование"])
        #expect(d[3].quote.hasPrefix("За деньгами приедет мой помощник"))
        #expect(d[4].labels == ["требование"])
    }

    @Test("Цитата, не найденная в разговоре, остаётся — без номера реплики")
    func demandsBank() throws {
        let v = try Mocks.state("fraud_bank_call")
        let d = ReportBuilder.demands(v.alarms)
        #expect(d.map(\.seq) == [4, 5, 7, 8, 9, nil])
        #expect(d[1].labels == ["перевод денег", "безопасный счёт"])
    }

    @Test("Текст для буфера обмена")
    func text() throws {
        let v = try Mocks.state("fraud_relative")
        let c = try #require(v.summary)
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let s = ReportBuilder.text(
            callCase: c, title: "Родственник в беде (ДТП)", stages: v.stages, demands: ReportBuilder.demands(v.alarms),
            hangup: v.hangup?.text, confidence: v.scheme?.percent, now: c.startedAt, calendar: cal
        )
        let lines = s.components(separatedBy: "\n")
        #expect(lines[0] == "Отчёт о звонке — Антимошенник")
        #expect(lines[1] == "Номер: +7 999 000-00-02")
        #expect(lines[2].hasPrefix("Когда: сегодня, 19:33"))
        #expect(lines[3] == "Вывод: мошенничество — Родственник в беде (ДТП), уверенность 95%")
        #expect(s.contains("Мошенник дошёл до этапа 2 из 4: «Следователь» или «адвокат»."))
        #expect(s.contains("Что требовал:\n— «"))
        #expect(s.hasSuffix("Что делать: Положите трубку и перезвоните родственнику на его обычный номер."))
    }

    @Test("Обычный звонок")
    func textNoFraud() throws {
        let v = try Mocks.state("no_fraud")
        let s = ReportBuilder.text(
            callCase: try #require(v.summary), title: "Обычный звонок", stages: [], demands: [], hangup: nil, confidence: nil
        )
        #expect(s.contains("Вывод: признаков мошенничества не найдено"))
        #expect(!s.contains("Что делать"))
    }
}
