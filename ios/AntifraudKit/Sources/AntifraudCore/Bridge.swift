import Foundation

// Ответы моста ui/server.py (кроме событий).

/// Тестовый звонок из репозитория: GET /api/sources → calls.
public struct CallFile: Codable, Sendable, Hashable, Identifiable {
    public var path: String
    public var title: String
    public var phone: String?
    public var schemeID: String?
    /// audio — запись (нужен Git LFS и распознавание), transcript — текст.
    public var kind: String

    public var id: String { path }

    enum CodingKeys: String, CodingKey {
        case path, title, phone, schemeID = "scheme_id", kind
    }
}

/// Записанный звонок для проигрывания без модели.
public struct RecordEntry: Codable, Sendable, Hashable, Identifiable {
    public var path: String
    public var title: String

    public var id: String { path }
}

/// GET /api/sources.
public struct Sources: Codable, Sendable, Hashable {
    public var calls: [CallFile]
    public var records: [RecordEntry]
    /// Идёт ли звонок: его call_id или nil.
    public var busy: String?
}

/// Состояние процесса звонка: ответ POST /api/calls, /stop, GET /api/calls/<id> и событие process в SSE.
public struct CallStatus: Codable, Sendable, Hashable {
    public var callID: String
    /// file / mic / loopback / replay / remote; nil — звонок запускал прошлый сервер.
    public var source: String?
    public var running: Bool
    public var stopping: Bool
    public var returncode: Int?
    public var error: String

    enum CodingKeys: String, CodingKey {
        case callID = "call_id", source, running, stopping, returncode, error
    }

    public init(callID: String, source: String?, running: Bool, stopping: Bool, returncode: Int?, error: String) {
        self.callID = callID
        self.source = source
        self.running = running
        self.stopping = stopping
        self.returncode = returncode
        self.error = error
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        callID = try c.decode(String.self, forKey: .callID)
        source = try c.decodeIfPresent(String.self, forKey: .source)
        running = try c.decodeIfPresent(Bool.self, forKey: .running) ?? false
        stopping = try c.decodeIfPresent(Bool.self, forKey: .stopping) ?? false
        returncode = try c.decodeIfPresent(Int.self, forKey: .returncode)
        error = try c.decodeIfPresent(String.self, forKey: .error) ?? ""
    }
}

/// Звонок этого компьютера из data/runs/: GET /api/runs.
public struct Run: Codable, Sendable, Hashable, Identifiable {
    public struct SchemeRef: Codable, Sendable, Hashable {
        public var id: String
        public var name: String
        public var confidence: Double
    }

    public var callID: String
    public var startedAt: Date
    public var lastAt: Date?
    public var phone: String?
    public var running: Bool
    public var ended: Bool
    public var summary: CallCase?
    public var scheme: SchemeRef?
    public var warnings: Int
    public var lines: Int
    public var replay: Bool

    public var id: String { callID }

    enum CodingKeys: String, CodingKey {
        case callID = "call_id", startedAt = "started_at", lastAt = "last_at", phone, running, ended, summary, scheme
        case warnings, lines, replay
    }

    public init(
        callID: String, startedAt: Date, lastAt: Date? = nil, phone: String? = nil, running: Bool = false,
        ended: Bool = true, summary: CallCase? = nil, scheme: SchemeRef? = nil, warnings: Int = 0, lines: Int = 0,
        replay: Bool = false
    ) {
        self.callID = callID
        self.startedAt = startedAt
        self.lastAt = lastAt
        self.phone = phone
        self.running = running
        self.ended = ended
        self.summary = summary
        self.scheme = scheme
        self.warnings = warnings
        self.lines = lines
        self.replay = replay
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        callID = try c.decode(String.self, forKey: .callID)
        startedAt = try c.decode(Date.self, forKey: .startedAt)
        lastAt = try c.decodeIfPresent(Date.self, forKey: .lastAt)
        phone = try c.decodeIfPresent(String.self, forKey: .phone)
        running = try c.decodeIfPresent(Bool.self, forKey: .running) ?? false
        ended = try c.decodeIfPresent(Bool.self, forKey: .ended) ?? false
        // Отчёт в логе мог быть записан старой версией агента — не роняем из-за него весь список.
        summary = try? c.decodeIfPresent(CallCase.self, forKey: .summary)
        scheme = try? c.decodeIfPresent(SchemeRef.self, forKey: .scheme)
        warnings = try c.decodeIfPresent(Int.self, forKey: .warnings) ?? 0
        lines = try c.decodeIfPresent(Int.self, forKey: .lines) ?? 0
        replay = try c.decodeIfPresent(Bool.self, forKey: .replay) ?? false
    }
}

/// Одна проверка готовности: GET /api/health → checks.
public struct HealthCheck: Codable, Sendable, Hashable, Identifiable {
    public var key: String
    public var label: String
    public var ok: Bool
    public var detail: String
    public var fix: String

    public var id: String { key }

    public init(key: String, label: String, ok: Bool, detail: String, fix: String) {
        self.key = key
        self.label = label
        self.ok = ok
        self.detail = detail
        self.fix = fix
    }
}

/// GET /api/health: готов ли компьютер к звонку через модель.
public struct Health: Codable, Sendable, Hashable {
    public var modelReady: Bool
    public var checks: [HealthCheck]

    enum CodingKeys: String, CodingKey {
        case modelReady = "model_ready", checks
    }

    public init(modelReady: Bool, checks: [HealthCheck]) {
        self.modelReady = modelReady
        self.checks = checks
    }

    /// Чего не хватает для звонка, как blockers() в NewCall.jsx: записи — ничего,
    /// аудиофайлам из репозитория — ещё и Git LFS, звуку (файл или микрофон) — распознавание речи.
    public func blockers(lfs: Bool = false, asr: Bool? = nil) -> [HealthCheck] {
        var need: Set<String> = ["deps", "llm", "embed", "index"]
        if lfs { need.insert("audio") }
        if asr ?? lfs { need.insert("asr") }
        return checks.filter { need.contains($0.key) && !$0.ok }
    }
}

/// GET /api/info — без кода доступа: проверка, что по адресу наш сервер.
public struct ServerInfo: Codable, Sendable, Hashable {
    public var app: String
    public var api: Int
    public var auth: Bool

    public init(app: String, api: Int, auth: Bool) {
        self.app = app
        self.api = api
        self.auth = auth
    }

    /// Версия API, под которую написано приложение.
    public static let supportedAPI = 1

    public var isAntifraud: Bool { app == "antifraud" }
}

/// GET /api/numbers/<phone>.
public struct NumberHistory: Codable, Sendable, Hashable {
    public var info: NumberInfo
    public var cases: [CallCase]
}

/// POST /api/upload?name=… → путь для запуска звонка.
public struct UploadResult: Codable, Sendable, Hashable {
    public var path: String
    public var name: String
    public var size: Int
}

/// Тело POST /api/calls.
public struct StartCallRequest: Codable, Sendable, Hashable {
    public var source: String
    public var path: String?
    public var phone: String?
    public var realtime: Bool?
    public var speed: Double?

    public init(source: String, path: String? = nil, phone: String? = nil, realtime: Bool? = nil, speed: Double? = nil) {
        self.source = source
        self.path = path
        self.phone = phone
        self.realtime = realtime
        self.speed = speed
    }

    /// Живой звонок со звуком от телефона.
    public static func remote(phone: String?) -> StartCallRequest {
        StartCallRequest(source: "remote", phone: phone)
    }

    public static func file(path: String, phone: String? = nil, realtime: Bool = true) -> StartCallRequest {
        StartCallRequest(source: "file", path: path, phone: phone, realtime: realtime)
    }

    public static func replay(path: String, speed: Double = 1) -> StartCallRequest {
        StartCallRequest(source: "replay", path: path, speed: speed)
    }
}
