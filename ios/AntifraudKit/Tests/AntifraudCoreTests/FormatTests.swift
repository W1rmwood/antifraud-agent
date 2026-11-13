import Foundation
import Testing
@testable import AntifraudCore

@Suite("Номер, время, слова")
struct FormatTests {
    @Test("Нормализация номера как storage/db.py", arguments: [
        ("+7 999 000-00-01", "+79990000001"),
        ("8 (999) 000-00-01", "+79990000001"),
        ("79990000001", "+79990000001"),
        ("9990000001", "+79990000001"),
        ("  +7.999.000.00.01 ", "+79990000001"),
    ])
    func normalize(raw: String, expected: String) throws {
        #expect(try PhoneNumber.normalize(raw) == expected)
    }

    @Test("Пустой номер — nil, неверный — ошибка")
    func normalizeErrors() throws {
        #expect(try PhoneNumber.normalize("") == nil)
        #expect(try PhoneNumber.normalize("   ") == nil)
        #expect(throws: PhoneNumber.Failure.invalidCharacters) { try PhoneNumber.normalize("8 999 abc") }
        #expect(throws: PhoneNumber.Failure.invalidCharacters) { try PhoneNumber.normalize("7+9990000001") }
        #expect(throws: PhoneNumber.Failure.noDigits) { try PhoneNumber.normalize("()-") }
        #expect(throws: PhoneNumber.Failure.notRussian) { try PhoneNumber.normalize("+380501234567") }
        #expect(throws: PhoneNumber.Failure.notRussian) { try PhoneNumber.normalize("12345") }
    }

    @Test("Показ номера")
    func phone() {
        #expect(Format.phone("+79990000002") == "+7 999 000-00-02")
        #expect(Format.phone(nil) == nil)
        #expect(Format.phone("") == nil)
        #expect(Format.phone("12345") == "12345")
    }

    @Test("Склонение")
    func plural() {
        let f = (one: "раз", few: "раза", many: "раз")
        #expect(Format.plural(1, f) == "раз")
        #expect(Format.plural(2, f) == "раза")
        #expect(Format.plural(5, f) == "раз")
        #expect(Format.plural(11, f) == "раз")
        #expect(Format.plural(22, f) == "раза")
        #expect(Format.count(21, (one: "звонок", few: "звонка", many: "звонков")) == "21 звонок")
    }

    @Test("Время и длительность")
    func clock() {
        #expect(Format.clock(0) == "00:00")
        #expect(Format.clock(187.9) == "03:07")
        #expect(Format.clock(-5) == "00:00")
        #expect(Format.clock(nil) == "00:00")
        #expect(Format.seconds(0.3) == "0,3 с")
        #expect(Format.seconds(19.4) == "19 с")
        #expect(Format.seconds(nil) == "")
        #expect(Format.ms(12.4) == "12 мс")
        #expect(Format.ms(2500) == "2,5 с")
        let a = ISODate.parse("2026-10-29T19:30:00Z")!
        let b = ISODate.parse("2026-10-29T19:33:07.5Z")!
        #expect(Format.duration(a, b) == "03:07")
        #expect(Format.duration(a, nil) == "")
    }

    @Test("«сегодня» и дата")
    func when() {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let now = ISODate.parse("2026-10-29T23:00:00Z")!
        #expect(Format.when(ISODate.parse("2026-10-29T22:33:00Z"), now: now, calendar: cal) == "сегодня, 22:33")
        let other = Format.when(ISODate.parse("2026-10-20T08:05:00Z"), now: now, calendar: cal)
        #expect(other.hasPrefix("20 окт"))
        #expect(other.hasSuffix(", 08:05"))
        #expect(Format.when(nil) == "")
    }

    @Test("Даты ISO 8601 в разных видах")
    func isoDates() {
        let base = ISODate.parse("2026-10-29T19:30:44Z")!
        #expect(base.timeIntervalSince1970 == 1_793_302_244)
        #expect(ISODate.parse("2026-10-29T19:30:44") == base)
        #expect(ISODate.parse("2026-10-29T22:30:44+03:00") == base)
        #expect(ISODate.parse("2026-10-29T19:30:44.175452+00:00")!.timeIntervalSince(base) - 0.175452 < 1e-6)
        #expect(ISODate.parse("2026-10-29") == ISODate.parse("2026-10-29T00:00:00Z"))
        #expect(ISODate.parse("29.10.2026") == nil)
        #expect(ISODate.parse("2026-10-29T19:30:44Zx") == nil)
    }

    @Test("Адрес сервера, введённый руками")
    func address() {
        #expect(ServerConfig.parseAddress("192.168.1.5")?.absoluteString == "http://192.168.1.5:8642")
        #expect(ServerConfig.parseAddress(" 192.168.1.5:9000 ")?.absoluteString == "http://192.168.1.5:9000")
        #expect(ServerConfig.parseAddress("http://mac.local:8642/")?.absoluteString == "http://mac.local:8642")
        #expect(ServerConfig.parseAddress("https://example.test")?.absoluteString == "https://example.test")
        #expect(ServerConfig.parseAddress("") == nil)
        #expect(ServerConfig.parseAddress("ftp://x") == nil)
    }

    @Test("Причина падения агента по stderr")
    func diagnose() {
        #expect(Diagnose.explain("ModuleNotFoundError: No module named 'chromadb'").reason.contains("chromadb"))
        #expect(Diagnose.explain("httpx.ConnectError: [Errno 61] Connection refused").fix == "ollama serve")
        #expect(Diagnose.explain("model 'qwen3:8b' not found").fix == "ollama pull qwen3:8b")
        #expect(Diagnose.explain("что-то странное").fix == nil)
    }

    @Test("Сохранение сервера")
    func store() {
        let store = ServerStore(suiteName: "antifraud.tests.\(UUID().uuidString)")
        #expect(store.load() == nil)
        let config = ServerConfig(baseURL: URL(string: "http://192.168.1.5:8642")!, token: "123456")
        store.save(config)
        #expect(store.load() == config)
        #expect(store.showProvoke)
        store.showProvoke = false
        #expect(!store.showProvoke)
        store.clear()
        #expect(store.load() == nil)
    }

    @Test("Готовность сервера: чего не хватает для звонка")
    func blockers() {
        let health = Health(modelReady: false, checks: [
            HealthCheck(key: "deps", label: "Зависимости", ok: true, detail: "", fix: ""),
            HealthCheck(key: "llm", label: "Модель", ok: false, detail: "не скачана", fix: "ollama pull qwen3:8b"),
            HealthCheck(key: "audio", label: "Записи", ok: false, detail: "", fix: ""),
            HealthCheck(key: "asr", label: "Распознавание", ok: false, detail: "", fix: ""),
        ])
        #expect(health.blockers().map(\.key) == ["llm"])
        #expect(health.blockers(asr: true).map(\.key) == ["llm", "asr"])
        #expect(health.blockers(lfs: true).map(\.key) == ["llm", "audio", "asr"])
    }
}

@Suite("Слияние истории")
struct HistoryTests {
    func date(_ s: String) -> Date { ISODate.parse(s)! }

    func makeCase(_ id: String, _ started: String, scheme: String? = nil, phone: String? = "+79990000001") -> CallCase {
        CallCase(
            callID: id, phone: phone, startedAt: date(started), endedAt: nil, schemeID: scheme, maxStage: scheme == nil ? nil : 2,
            legend: nil, keyPhrases: [], summary: "", transcriptPath: ""
        )
    }

    @Test("Кейсы и звонки из логов одним списком, новые сверху, без повторов")
    func merge() {
        let cases = [
            makeCase("a", "2026-10-29T10:00:00Z", scheme: "safe-account"),
            makeCase("b", "2026-10-28T10:00:00Z"),
        ]
        let runs = [
            // Тот же звонок, что кейс «a»: берётся из лога, второй раз не показывается.
            Run(callID: "a", startedAt: date("2026-10-29T10:00:00Z"), summary: makeCase("a", "2026-10-29T09:59:00Z", scheme: "safe-account")),
            // Запись: отчёт от другого звонка.
            Run(callID: "r", startedAt: date("2026-10-30T10:00:00Z"), summary: makeCase("demo", "2026-10-01T10:00:00Z", scheme: "relative-accident"), replay: true),
            // Идёт сейчас, отчёта ещё нет.
            Run(callID: "live", startedAt: date("2026-10-31T10:00:00Z"), phone: "+79990000005", running: true, ended: false),
            // Оборвался.
            Run(callID: "dead", startedAt: date("2026-10-27T10:00:00Z"), ended: false),
        ]
        let merged = mergeHistory(cases: cases, runs: runs)
        #expect(merged.map(\.callID) == ["live", "r", "a", "b", "dead"])
        #expect(merged[0].noReport && merged[0].running)
        #expect(merged[0].phone == "+79990000005")
        #expect(merged[0].title(schemeName: nil) == "Идёт сейчас")
        #expect(merged[1].replay)
        #expect(merged[1].schemeID == "relative-accident")
        // Время начала — из лога, а не из отчёта.
        #expect(merged[2].startedAt == date("2026-10-29T10:00:00Z"))
        #expect(merged[2].title(schemeName: "Безопасный счёт") == "Безопасный счёт")
        #expect(merged[3].title(schemeName: nil) == "Обычный звонок")
        #expect(merged[4].title(schemeName: nil) == "Прервался")
    }
}
