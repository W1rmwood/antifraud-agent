import Foundation

// События агента (контракт №3) и их payload — как PAYLOAD_MODELS в contracts/models.py.

public struct CallStartedPayload: Codable, Sendable, Hashable {
    public var phone: String?
    public var source: AudioSourceInfo?
}

public struct SchemeDetectedPayload: Codable, Sendable, Hashable {
    public var schemeID: String
    public var schemeName: String
    public var confidence: Double
    public var evidence: [String]
    /// Все этапы схемы; пусто в логах до 30.10.
    public var stages: [StageInfo]

    enum CodingKeys: String, CodingKey {
        case schemeID = "scheme_id", schemeName = "scheme_name", confidence, evidence, stages
    }

    public init(schemeID: String, schemeName: String, confidence: Double, evidence: [String], stages: [StageInfo]) {
        self.schemeID = schemeID
        self.schemeName = schemeName
        self.confidence = confidence
        self.evidence = evidence
        self.stages = stages
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schemeID = try c.decode(String.self, forKey: .schemeID)
        schemeName = try c.decode(String.self, forKey: .schemeName)
        confidence = try c.decode(Double.self, forKey: .confidence)
        evidence = try c.decodeIfPresent([String].self, forKey: .evidence) ?? []
        stages = try c.decodeIfPresent([StageInfo].self, forKey: .stages) ?? []
    }
}

public struct StageChangedPayload: Codable, Sendable, Hashable {
    public var schemeID: String
    public var stageN: Int
    public var stageName: String
    public var nextMove: String

    enum CodingKeys: String, CodingKey {
        case schemeID = "scheme_id", stageN = "stage_n", stageName = "stage_name", nextMove = "next_move"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schemeID = try c.decode(String.self, forKey: .schemeID)
        stageN = try c.decode(Int.self, forKey: .stageN)
        stageName = try c.decode(String.self, forKey: .stageName)
        nextMove = try c.decodeIfPresent(String.self, forKey: .nextMove) ?? ""
    }
}

/// Вид подсказки: stall — тянуть время, question — уточнить, provoke — провокация, hangup — выход.
public enum SuggestionKind: String, Codable, Sendable, Hashable {
    case stall, question, provoke, hangup

    /// Подпись, как в вебе (format.js: KINDS).
    public var title: String {
        switch self {
        case .stall: "тянуть время"
        case .question: "уточнить"
        case .provoke: "провокация"
        case .hangup: "выход"
        }
    }
}

public struct SuggestionPayload: Codable, Sendable, Hashable {
    public var text: String
    public var kind: SuggestionKind
    public var tactic: String?
}

public struct WarningPayload: Codable, Sendable, Hashable {
    public var text: String
}

public struct ToolCallPayload: Codable, Sendable, Hashable {
    public var tool: String
    public var args: [String: JSONValue]

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tool = try c.decode(String.self, forKey: .tool)
        args = try c.decodeIfPresent([String: JSONValue].self, forKey: .args) ?? [:]
    }
}

public struct ToolResultPayload: Codable, Sendable, Hashable {
    public var tool: String
    public var ok: Bool
    public var result: JSONValue

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tool = try c.decode(String.self, forKey: .tool)
        ok = try c.decode(Bool.self, forKey: .ok)
        result = try c.decodeIfPresent(JSONValue.self, forKey: .result) ?? .null
    }
}

/// Payload события, разобранный по его типу.
public enum EventPayload: Sendable, Hashable {
    case callStarted(CallStartedPayload)
    case transcript(Utterance)
    case schemeDetected(SchemeDetectedPayload)
    case stageChanged(StageChangedPayload)
    case suggestion(SuggestionPayload)
    case warning(WarningPayload)
    case toolCall(ToolCallPayload)
    case toolResult(ToolResultPayload)
    case summary(CallCase)
    case callEnded
    /// Тип, которого приложение не знает (или payload не той формы): событие не теряется и не роняет разбор.
    case unknown(type: String, payload: JSONValue)
}

public struct AgentEvent: Decodable, Sendable, Hashable {
    public var callID: String
    /// Номер события в звонке; nil — в логах до 30.10.
    public var seq: Int?
    public var ts: Date
    public var type: String
    public var payload: EventPayload

    enum CodingKeys: String, CodingKey {
        case callID = "call_id", seq, ts, type, payload
    }

    public init(callID: String, seq: Int?, ts: Date, type: String, payload: EventPayload) {
        self.callID = callID
        self.seq = seq
        self.ts = ts
        self.type = type
        self.payload = payload
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        callID = try c.decode(String.self, forKey: .callID)
        seq = try c.decodeIfPresent(Int.self, forKey: .seq)
        ts = try c.decode(Date.self, forKey: .ts)
        type = try c.decode(String.self, forKey: .type)
        payload = Self.decodePayload(type: type, container: c)
    }

    private static func decodePayload(type: String, container c: KeyedDecodingContainer<CodingKeys>) -> EventPayload {
        func typed<T: Decodable>(_: T.Type) -> T? { try? c.decode(T.self, forKey: .payload) }
        let parsed: EventPayload? = switch type {
        case "call_started": typed(CallStartedPayload.self).map(EventPayload.callStarted)
        case "transcript": typed(Utterance.self).map(EventPayload.transcript)
        case "scheme_detected": typed(SchemeDetectedPayload.self).map(EventPayload.schemeDetected)
        case "stage_changed": typed(StageChangedPayload.self).map(EventPayload.stageChanged)
        case "suggestion": typed(SuggestionPayload.self).map(EventPayload.suggestion)
        case "warning": typed(WarningPayload.self).map(EventPayload.warning)
        case "tool_call": typed(ToolCallPayload.self).map(EventPayload.toolCall)
        case "tool_result": typed(ToolResultPayload.self).map(EventPayload.toolResult)
        case "summary": typed(CallCase.self).map(EventPayload.summary)
        case "call_ended": .callEnded
        default: nil
        }
        return parsed ?? .unknown(type: type, payload: (try? c.decode(JSONValue.self, forKey: .payload)) ?? .null)
    }

    /// Строка лога / data из SSE → событие.
    public static func decode(_ data: Data) throws -> AgentEvent {
        try JSONCoding.decoder().decode(AgentEvent.self, from: data)
    }

    /// Файл .jsonl целиком: по событию на строку, пустые строки пропускаются.
    public static func decodeLines(_ text: String) throws -> [AgentEvent] {
        let decoder = JSONCoding.decoder()
        return try text.split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
            .map { try decoder.decode(AgentEvent.self, from: Data($0.utf8)) }
    }
}
