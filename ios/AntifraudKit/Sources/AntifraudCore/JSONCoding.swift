import Foundation

/// Даты сервера: ISO 8601 с долями секунды и без, с «Z», со смещением «+00:00» или вовсе без зоны
/// (так Python пишет first_seen из базы). Без зоны — UTC: агент пишет время в UTC.
public enum ISODate {
    public static func parse(_ text: String) -> Date? {
        let s = Array(text.utf8)
        var i = 0
        func digits(_ n: Int) -> Int? {
            guard i + n <= s.count else { return nil }
            var v = 0
            for k in i..<(i + n) {
                let c = s[k]
                guard c >= 48, c <= 57 else { return nil }
                v = v * 10 + Int(c - 48)
            }
            i += n
            return v
        }
        func expect(_ chars: String) -> Bool {
            guard i < s.count, chars.utf8.contains(s[i]) else { return false }
            i += 1
            return true
        }
        guard let year = digits(4), expect("-"), let month = digits(2), expect("-"), let day = digits(2) else {
            return nil
        }
        var hour = 0, minute = 0, second = 0
        var fraction = 0.0
        var offset = 0
        if i < s.count {
            guard expect("T t"), let h = digits(2), expect(":"), let m = digits(2) else { return nil }
            hour = h
            minute = m
            if i < s.count, s[i] == UInt8(ascii: ":") {
                i += 1
                guard let sec = digits(2) else { return nil }
                second = sec
                if i < s.count, s[i] == UInt8(ascii: ".") || s[i] == UInt8(ascii: ",") {
                    i += 1
                    var scale = 0.1
                    let start = i
                    while i < s.count, s[i] >= 48, s[i] <= 57 {
                        fraction += Double(s[i] - 48) * scale
                        scale /= 10
                        i += 1
                    }
                    guard i > start else { return nil }
                }
            }
            if i < s.count {
                if s[i] == UInt8(ascii: "Z") || s[i] == UInt8(ascii: "z") {
                    i += 1
                } else if s[i] == UInt8(ascii: "+") || s[i] == UInt8(ascii: "-") {
                    let sign = s[i] == UInt8(ascii: "-") ? -1 : 1
                    i += 1
                    guard let oh = digits(2) else { return nil }
                    if i < s.count, s[i] == UInt8(ascii: ":") { i += 1 }
                    let om = digits(2) ?? 0
                    offset = sign * (oh * 3600 + om * 60)
                } else {
                    return nil
                }
            }
        }
        guard i == s.count, (1...12).contains(month), (1...31).contains(day), hour < 24, minute < 60, second < 61 else {
            return nil
        }
        let days = daysFromCivil(year: year, month: month, day: day)
        let seconds = Double(days * 86400 + hour * 3600 + minute * 60 + second - offset) + fraction
        return Date(timeIntervalSince1970: seconds)
    }

    /// Дата → «2026-10-29T19:30:44.175Z».
    public static func format(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.string(from: date)
    }

    // Число дней от 1970-01-01 (алгоритм Говарда Хиннанта), чтобы не зависеть от Calendar и локали.
    private static func daysFromCivil(year: Int, month: Int, day: Int) -> Int {
        let y = month <= 2 ? year - 1 : year
        let era = (y >= 0 ? y : y - 399) / 400
        let yoe = y - era * 400
        let mp = (month + 9) % 12
        let doy = (153 * mp + 2) / 5 + day - 1
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
        return era * 146097 + doe - 719468
    }
}

/// Один настроенный JSONDecoder/JSONEncoder на весь обмен с сервером.
public enum JSONCoding {
    public static func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { decoder in
            let c = try decoder.singleValueContainer()
            let text = try c.decode(String.self)
            guard let date = ISODate.parse(text) else {
                throw DecodingError.dataCorruptedError(in: c, debugDescription: "Не дата ISO 8601: \(text)")
            }
            return date
        }
        return d
    }

    public static func encoder() -> JSONEncoder {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .custom { date, encoder in
            var c = encoder.singleValueContainer()
            try c.encode(ISODate.format(date))
        }
        return e
    }
}
