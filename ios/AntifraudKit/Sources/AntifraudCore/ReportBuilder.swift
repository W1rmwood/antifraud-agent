import Foundation

/// Требования мошенника и текст отчёта — чистая часть ui/web/src/screens/Report.jsx.
public enum ReportBuilder {
    public struct Demand: Sendable, Hashable {
        public var quote: String
        public var seq: Int?
        public var at: Double
        public var labels: [String]
    }

    /// Что требовал мошенник: цитаты из плашек. Одна фраза могла попасть и в правило, и в требование
    /// модели — показываем её один раз с обеими метками; правило цитирует предложение, модель — реплику,
    /// поэтому из двух вариантов одной фразы остаётся полный.
    public static func demands(_ alarms: [WarningItem]) -> [Demand] {
        func norm(_ q: String) -> String {
            var s = q.lowercased().replacingOccurrences(of: "ё", with: "е")
            while let last = s.last, last.isWhitespace || ".!?…,;:".contains(last) { s.removeLast() }
            return s
        }
        var out: [Demand] = []
        for w in alarms {
            let labels = w.kind == .rule ? w.labels : ["требование"]
            for h in w.hits {
                let a = norm(h.quote)
                if let i = out.firstIndex(where: { d in
                    let q = norm(d.quote)
                    return q == a || (d.seq != nil && d.seq == h.seq && (q.contains(a) || a.contains(q)))
                }) {
                    var seen = Set<String>()
                    out[i].labels = (out[i].labels + labels).filter { seen.insert($0).inserted }
                    if h.quote.count > out[i].quote.count { out[i].quote = h.quote }
                } else {
                    out.append(Demand(quote: h.quote, seq: h.seq, at: h.at, labels: labels))
                }
            }
        }
        // Стабильная сортировка по времени, как Array.sort в JS.
        return out.enumerated().sorted { $0.element.at != $1.element.at ? $0.element.at < $1.element.at : $0.offset < $1.offset }
            .map(\.element)
    }

    /// Текст отчёта для буфера обмена: переслать родственникам или приложить к заявлению.
    public static func text(
        callCase c: CallCase, title: String, stages: [StageInfo], demands: [Demand], hangup: String?, confidence: Int?,
        now: Date = Date(), calendar: Calendar = .current
    ) -> String {
        let fraud = c.schemeID != nil
        var out = ["Отчёт о звонке — Антимошенник"]
        out.append("Номер: \(Format.phone(c.phone) ?? "неизвестен")")
        let length = c.endedAt != nil ? ", длительность \(Format.duration(c.startedAt, c.endedAt))" : ""
        out.append("Когда: \(Format.when(c.startedAt, now: now, calendar: calendar))\(length)")
        let conf = confidence.map { ", уверенность \($0)%" } ?? ""
        out.append("Вывод: \(fraud ? "мошенничество — \(title)\(conf)" : "признаков мошенничества не найдено")")
        if fraud, let legend = c.legend, !legend.isEmpty { out.append("Кем представлялись: \(legend)") }
        if !c.summary.isEmpty { out += ["", "Кратко: \(c.summary)"] }
        if fraud, let reached = c.maxStage, reached >= 1, reached <= stages.count {
            out.append("Мошенник дошёл до этапа \(reached) из \(stages.count): \(stages[reached - 1].name).")
        }
        if !demands.isEmpty { out += ["", "Что требовал:"] + demands.map { "— «\($0.quote)»" } }
        if let hangup { out += ["", "Что делать: \(hangup)"] }
        return out.joined(separator: "\n")
    }
}
