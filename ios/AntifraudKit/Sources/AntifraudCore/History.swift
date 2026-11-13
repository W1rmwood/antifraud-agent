import Foundation

/// Строка истории: кейс из базы или звонок из data/runs/ без отчёта.
public struct HistoryItem: Sendable, Hashable, Identifiable {
    public var callID: String
    public var phone: String?
    public var startedAt: Date
    public var schemeID: String?
    public var maxStage: Int?
    public var legend: String?
    /// Отчёта нет: звонок идёт, прервался или закончился без summary.
    public var noReport: Bool
    public var ended: Bool
    public var running: Bool
    /// Запись, проигранная без модели: в базу не сохраняется.
    public var replay: Bool
    public var callCase: CallCase?

    public var id: String { callID }

    public init(
        callID: String, phone: String?, startedAt: Date, schemeID: String?, maxStage: Int?, legend: String?,
        noReport: Bool, ended: Bool, running: Bool, replay: Bool, callCase: CallCase?
    ) {
        self.callID = callID
        self.phone = phone
        self.startedAt = startedAt
        self.schemeID = schemeID
        self.maxStage = maxStage
        self.legend = legend
        self.noReport = noReport
        self.ended = ended
        self.running = running
        self.replay = replay
        self.callCase = callCase
    }

    /// Кейс из базы.
    public init(case c: CallCase) {
        self.init(
            callID: c.callID, phone: c.phone, startedAt: c.startedAt, schemeID: c.schemeID, maxStage: c.maxStage,
            legend: c.legend, noReport: false, ended: true, running: false, replay: false, callCase: c
        )
    }

    /// Подпись строки, как в History.jsx.
    public func title(schemeName: String?) -> String {
        if noReport { return running ? "Идёт сейчас" : ended ? "Без отчёта" : "Прервался" }
        if let schemeID { return schemeName ?? schemeID }
        return "Обычный звонок"
    }
}

/// Кейсы из базы и звонки из data/runs/ одним списком, новые сверху — как mergeHistory в api.js.
/// Запись (проигрывание) кейс в базу не сохраняет, поэтому без логов её не было бы в истории.
public func mergeHistory(cases: [CallCase], runs: [Run]) -> [HistoryItem] {
    let fromRuns: [HistoryItem] = runs.map { r in
        if let s = r.summary {
            return HistoryItem(
                callID: r.callID, phone: s.phone, startedAt: r.startedAt, schemeID: s.schemeID, maxStage: s.maxStage,
                legend: s.legend, noReport: false, ended: true, running: false, replay: r.replay, callCase: s
            )
        }
        return HistoryItem(
            callID: r.callID, phone: r.phone, startedAt: r.startedAt, schemeID: nil, maxStage: nil, legend: nil,
            noReport: true, ended: r.ended, running: r.running, replay: false, callCase: nil
        )
    }
    let seen = Set(fromRuns.map(\.callID))
    let fromDB = cases.filter { !seen.contains($0.callID) }.map { HistoryItem(case: $0) }
    // Стабильная сортировка: при равном времени порядок как в исходных списках (как Array.sort в JS).
    return (fromRuns + fromDB).enumerated()
        .sorted { a, b in
            a.element.startedAt != b.element.startedAt ? a.element.startedAt > b.element.startedAt : a.offset < b.offset
        }
        .map(\.element)
}

/// Почему упал агент — по последним строкам stderr (перенос ui/web/src/diagnose.js).
public enum Diagnose {
    public struct Result: Sendable, Equatable {
        public var reason: String
        public var fix: String?
    }

    public static func explain(_ errorText: String?) -> Result {
        let text = errorText ?? ""
        func first(_ pattern: String, _ options: NSRegularExpression.Options = []) -> [String]? {
            guard let re = try? NSRegularExpression(pattern: pattern, options: options),
                  let m = re.firstMatch(in: text, range: NSRange(text.startIndex..., in: text))
            else { return nil }
            return (0..<m.numberOfRanges).map { i in
                Range(m.range(at: i), in: text).map { String(text[$0]) } ?? ""
            }
        }
        if let m = first(#"No module named '([\w.]+)'"#) {
            return Result(reason: "Не установлены зависимости агента: нет модуля \(m[1]).", fix: ".venv/bin/pip install -r requirements.txt")
        }
        if first(#"Connect(ion)?Error|Connection refused|ConnectError|11434"#) != nil {
            return Result(reason: "Не отвечает Ollama — модель не запущена.", fix: "ollama serve")
        }
        if let m = first(#"model ['"]?([\w.:-]+)['"]? not found"#, .caseInsensitive) {
            return Result(reason: "Модель \(m[1]) не скачана.", fix: "ollama pull \(m[1])")
        }
        if first(#"chroma|Collection .* does not exist"#, .caseInsensitive) != nil {
            return Result(reason: "Не собран индекс схем.", fix: ".venv/bin/python -m rag.index")
        }
        if first(#"PortAudio|Invalid input device|sounddevice|Error opening InputStream"#, .caseInsensitive) != nil {
            return Result(
                reason: "Нет доступа к микрофону или звуку компьютера.",
                fix: "Системные настройки → Конфиденциальность → Микрофон: разрешить терминалу"
            )
        }
        if first(#"LocalEntryNotFoundError|offline mode|HF_HUB_OFFLINE|snapshot"#, .caseInsensitive) != nil {
            return Result(
                reason: "Модель распознавания речи не скачана, а во время звонка интернет выключен.",
                fix: "скачать модель faster-whisper заранее, при включённом интернете"
            )
        }
        if first(#"Format not recognised|LibsndfileError|Error opening .*\.wav"#, .caseInsensitive) != nil {
            return Result(
                reason: "Запись звонка не скачана: вместо .wav в папке лежит указатель Git LFS.",
                fix: "git lfs install && git lfs pull"
            )
        }
        if let m = first(#"FileNotFoundError: .*?'([^']+)'"#) {
            return Result(reason: "Не найден файл \(m[1]).", fix: "git lfs pull — записи звонков лежат в Git LFS")
        }
        return Result(reason: "Агент завершился с ошибкой.", fix: nil)
    }
}
