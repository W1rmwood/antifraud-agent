import Foundation

// Модели контрактов (contracts/models.py) в том виде, в каком их отдаёт мост ui/server.py.
// Сервер — источник правды: модели здесь терпимы к лишним полям и не проверяют то, что уже проверил Pydantic.

/// Кто говорит. В живом звуке с телефона — всегда unknown: один канал, роли не разделены.
public enum Speaker: String, Codable, Sendable, Hashable, CaseIterable {
    case caller, user, unknown

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = Speaker(rawValue: raw) ?? .unknown
    }
}

/// Откуда звук: file, mic, loopback, remote (телефон). Вид — строкой: новый вид не ломает разбор.
public struct AudioSourceInfo: Codable, Sendable, Hashable {
    public var kind: String
    public var path: String?

    public init(kind: String, path: String? = nil) {
        self.kind = kind
        self.path = path
    }
}

public struct Utterance: Codable, Sendable, Hashable {
    public var callID: String
    public var seq: Int
    public var speaker: Speaker
    public var text: String
    public var tStart: Double
    public var tEnd: Double

    enum CodingKeys: String, CodingKey {
        case callID = "call_id", seq, speaker, text, tStart = "t_start", tEnd = "t_end"
    }

    public init(callID: String, seq: Int, speaker: Speaker, text: String, tStart: Double, tEnd: Double) {
        self.callID = callID
        self.seq = seq
        self.speaker = speaker
        self.text = text
        self.tStart = tStart
        self.tEnd = tEnd
    }
}

public struct StageInfo: Codable, Sendable, Hashable {
    public var n: Int
    public var name: String

    public init(n: Int, name: String) {
        self.n = n
        self.name = name
    }
}

/// Этап схемы целиком (карточка схемы).
public struct Stage: Codable, Sendable, Hashable {
    public var n: Int
    public var name: String
    public var goal: String
    public var typicalPhrases: [String]
    public var redFlags: [String]
    public var asksFor: [String]
    public var nextMove: String

    enum CodingKeys: String, CodingKey {
        case n, name, goal, typicalPhrases = "typical_phrases", redFlags = "red_flags", asksFor = "asks_for"
        case nextMove = "next_move"
    }
}

/// Категория схемы: под кого маскируется мошенник.
public enum SchemeCategory: String, Codable, Sendable, Hashable, CaseIterable {
    case bank, gosuslugi, relative, investment, delivery, other

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = SchemeCategory(rawValue: raw) ?? .other
    }

    /// Подпись категории, как в вебе (format.js: CATEGORIES).
    public var title: String {
        switch self {
        case .bank: "Банк"
        case .gosuslugi: "Госуслуги и госорганы"
        case .relative: "Родственники"
        case .investment: "Инвестиции"
        case .delivery: "Покупки и доставка"
        case .other: "Другое"
        }
    }
}

/// Схема целиком: GET /api/schemes/<id>.
public struct Scheme: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var name: String
    public var aliases: [String]
    public var category: SchemeCategory
    public var legend: String
    public var description: String
    public var stages: [Stage]
    public var pressurePoints: [String]
    public var stallTactics: [String]
    public var sources: [String]

    enum CodingKeys: String, CodingKey {
        case id, name, aliases, category, legend, description, stages
        case pressurePoints = "pressure_points", stallTactics = "stall_tactics", sources
    }
}

/// Строка каталога схем: GET /api/schemes (этапы — только номер и название).
public struct SchemeBrief: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var name: String
    public var aliases: [String]
    public var category: SchemeCategory
    public var legend: String
    public var description: String
    public var stages: [StageInfo]

    public init(
        id: String, name: String, aliases: [String] = [], category: SchemeCategory, legend: String,
        description: String, stages: [StageInfo]
    ) {
        self.id = id
        self.name = name
        self.aliases = aliases
        self.category = category
        self.legend = legend
        self.description = description
        self.stages = stages
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        aliases = try c.decodeIfPresent([String].self, forKey: .aliases) ?? []
        category = try c.decodeIfPresent(SchemeCategory.self, forKey: .category) ?? .other
        legend = try c.decodeIfPresent(String.self, forKey: .legend) ?? ""
        description = try c.decodeIfPresent(String.self, forKey: .description) ?? ""
        stages = try c.decodeIfPresent([StageInfo].self, forKey: .stages) ?? []
    }
}

/// История номера из хранилища. Неизвестный номер — callsCount = 0.
public struct NumberInfo: Codable, Sendable, Hashable {
    public var phone: String
    public var callsCount: Int
    public var firstSeen: Date?
    public var lastSeen: Date?
    public var schemes: [String]
    public var tags: [String]

    enum CodingKeys: String, CodingKey {
        case phone, callsCount = "calls_count", firstSeen = "first_seen", lastSeen = "last_seen", schemes, tags
    }
}

/// Отчёт о звонке (summary): кейс, который агент сохраняет в базу.
public struct CallCase: Codable, Sendable, Hashable, Identifiable {
    public var callID: String
    public var phone: String?
    public var startedAt: Date
    public var endedAt: Date?
    public var schemeID: String?
    public var maxStage: Int?
    public var legend: String?
    public var keyPhrases: [String]
    public var summary: String
    public var transcriptPath: String

    public var id: String { callID }

    enum CodingKeys: String, CodingKey {
        case callID = "call_id", phone, startedAt = "started_at", endedAt = "ended_at", schemeID = "scheme_id"
        case maxStage = "max_stage", legend, keyPhrases = "key_phrases", summary, transcriptPath = "transcript_path"
    }

    public init(
        callID: String, phone: String?, startedAt: Date, endedAt: Date?, schemeID: String?, maxStage: Int?,
        legend: String?, keyPhrases: [String], summary: String, transcriptPath: String
    ) {
        self.callID = callID
        self.phone = phone
        self.startedAt = startedAt
        self.endedAt = endedAt
        self.schemeID = schemeID
        self.maxStage = maxStage
        self.legend = legend
        self.keyPhrases = keyPhrases
        self.summary = summary
        self.transcriptPath = transcriptPath
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        callID = try c.decode(String.self, forKey: .callID)
        phone = try c.decodeIfPresent(String.self, forKey: .phone)
        startedAt = try c.decode(Date.self, forKey: .startedAt)
        endedAt = try c.decodeIfPresent(Date.self, forKey: .endedAt)
        schemeID = try c.decodeIfPresent(String.self, forKey: .schemeID)
        maxStage = try c.decodeIfPresent(Int.self, forKey: .maxStage)
        legend = try c.decodeIfPresent(String.self, forKey: .legend)
        keyPhrases = try c.decodeIfPresent([String].self, forKey: .keyPhrases) ?? []
        summary = try c.decodeIfPresent(String.self, forKey: .summary) ?? ""
        transcriptPath = try c.decodeIfPresent(String.self, forKey: .transcriptPath) ?? ""
    }
}
