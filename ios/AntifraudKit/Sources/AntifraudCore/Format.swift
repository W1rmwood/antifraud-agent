import Foundation

/// Как числа, время и номера выглядят на экране — перенос ui/web/src/format.js.
public enum Format {
    /// Секунды → «03:07».
    public static func clock(_ seconds: Double?) -> String {
        let s = max(0, Int((seconds ?? 0).rounded(.down)))
        return String(format: "%02d:%02d", s / 60, s % 60)
    }

    /// «+79990000002» → «+7 999 000-00-02». Неизвестный номер — nil, нестандартный — как есть.
    public static func phone(_ raw: String?) -> String? {
        guard let raw, !raw.isEmpty else { return nil }
        let chars = Array(raw)
        guard chars.count == 12, raw.hasPrefix("+7"), chars[2...].allSatisfy(\.isASCIIDigit) else { return raw }
        let d = String(chars[2...])
        func part(_ from: Int, _ len: Int) -> Substring {
            let start = d.index(d.startIndex, offsetBy: from)
            return d[start..<d.index(start, offsetBy: len)]
        }
        return "+7 \(part(0, 3)) \(part(3, 3))-\(part(6, 2))-\(part(8, 2))"
    }

    /// plural(2, ("раз", "раза", "раз")) → «раза».
    public static func plural(_ n: Int, _ forms: (one: String, few: String, many: String)) -> String {
        let d = abs(n) % 100
        let u = d % 10
        if d > 10 && d < 20 { return forms.many }
        if u == 1 { return forms.one }
        if u >= 2 && u <= 4 { return forms.few }
        return forms.many
    }

    /// «2 раза», «5 звонков».
    public static func count(_ n: Int, _ forms: (one: String, few: String, many: String)) -> String {
        "\(n) \(plural(n, forms))"
    }

    /// Секунды с запятой: 0.3 → «0,3 с», 19.4 → «19 с».
    public static func seconds(_ x: Double?) -> String {
        guard let x else { return "" }
        if x < 10 { return String(format: "%.1f с", x).replacingOccurrences(of: ".", with: ",") }
        return "\(Int(x.rounded())) с"
    }

    public static func ms(_ x: Double?) -> String {
        guard let x else { return "" }
        return x < 1000 ? "\(Int(x.rounded())) мс" : seconds(x / 1000)
    }

    /// «29 окт., 22:33»; сегодняшняя дата — «сегодня, 22:33».
    public static func when(_ date: Date?, now: Date = Date(), calendar: Calendar = .current) -> String {
        guard let date else { return "" }
        let locale = Locale(identifier: "ru_RU")
        let time = DateFormatter()
        time.locale = locale
        time.timeZone = calendar.timeZone
        time.dateFormat = "HH:mm"
        let dayText: String
        if calendar.isDate(date, inSameDayAs: now) {
            dayText = "сегодня"
        } else {
            let day = DateFormatter()
            day.locale = locale
            day.timeZone = calendar.timeZone
            day.setLocalizedDateFormatFromTemplate("dMMM")
            dayText = day.string(from: date)
        }
        return "\(dayText), \(time.string(from: date))"
    }

    /// Длительность звонка «03:07»; без конца — пусто.
    public static func duration(_ start: Date?, _ end: Date?) -> String {
        guard let start, let end else { return "" }
        return clock(end.timeIntervalSince(start))
    }

    /// Месяц для заголовка группы в истории: «Октябрь» или «Октябрь 2025» для прошлых лет.
    public static func month(_ date: Date, now: Date = Date(), calendar: Calendar = .current) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "ru_RU")
        f.timeZone = calendar.timeZone
        let sameYear = calendar.component(.year, from: date) == calendar.component(.year, from: now)
        f.dateFormat = sameYear ? "LLLL" : "LLLL yyyy"
        let s = f.string(from: date)
        return s.prefix(1).uppercased() + s.dropFirst()
    }

    /// Тактика подсказки (agent.prompts.TACTICS).
    public static func tactic(_ key: String?) -> String? {
        guard let key else { return nil }
        return [
            "play_dumb": "тупить",
            "fake_compliance": "ложное согласие",
            "absurd_story": "абсурдная история",
            "role_reversal": "перехват роли",
            "mockery": "издёвка",
        ][key]
    }

    /// «тянуть время: тупить».
    public static func suggestionLabel(kind: SuggestionKind, tactic key: String?) -> String {
        if let t = tactic(key) { return "\(kind.title): \(t)" }
        return kind.title
    }

    public static func speaker(_ s: Speaker) -> String {
        switch s {
        case .caller: "Звонящий"
        case .user: "Вы"
        case .unknown: "Голос"
        }
    }

    /// Метка номера в базе.
    public static func tag(_ key: String) -> String {
        [
            "fraud": "мошенник", "bank": "банк", "delivery": "доставка", "investment": "инвестиции",
            "courier-ok": "настоящий курьер",
        ][key] ?? key
    }

    /// Подпись источника звука, как в шапке звонка в вебе.
    public static func source(_ source: AudioSourceInfo?, replay: Bool) -> String? {
        if replay { return "запись, без модели" }
        guard let source else { return nil }
        switch source.kind {
        case "file": return source.path.map { ($0 as NSString).lastPathComponent } ?? "файл"
        case "mic": return "микрофон"
        case "loopback": return "звук компьютера"
        case "remote": return "микрофон телефона"
        default: return source.kind
        }
    }

    /// Размер файла: «12,3 МБ» или «840 КБ».
    public static func fileSize(_ bytes: Int) -> String {
        if bytes > 1_048_576 {
            return String(format: "%.1f МБ", Double(bytes) / 1_048_576).replacingOccurrences(of: ".", with: ",")
        }
        return "\(Int((Double(bytes) / 1024).rounded(.up))) КБ"
    }
}

extension Character {
    var isASCIIDigit: Bool { isASCII && isNumber }
}

/// Номер телефона: нормализация как storage/db.py: normalize_phone.
public enum PhoneNumber {
    public enum Failure: Error, Equatable, Sendable, LocalizedError {
        case invalidCharacters, noDigits, notRussian

        public var errorDescription: String? {
            switch self {
            case .invalidCharacters: "В номере недопустимые символы"
            case .noDigits: "В номере нет цифр"
            case .notRussian: "Номер не российский или неверной длины: нужно +7 и 10 цифр"
            }
        }
    }

    /// «8 (999) 000-00-01» → «+79990000001». Пустая строка — nil (номер необязателен).
    public static func normalize(_ raw: String) throws(Failure) -> String? {
        let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return nil }
        let allowed = Set("0123456789()-+. \t")
        guard s.allSatisfy({ allowed.contains($0) || $0.isWhitespace }), !s.dropFirst().contains("+") else {
            throw .invalidCharacters
        }
        let digits = String(s.filter(\.isASCIIDigit))
        guard !digits.isEmpty else { throw .noDigits }
        let result: String
        if s.hasPrefix("+") {
            result = "+" + digits
        } else if digits.count == 11, let first = digits.first, first == "7" || first == "8" {
            result = "+7" + digits.dropFirst()
        } else if digits.count == 10, digits.first == "9" {
            result = "+7" + digits
        } else {
            result = "+" + digits
        }
        guard result.count == 12, result.hasPrefix("+7") else { throw .notRussian }
        return result
    }
}
