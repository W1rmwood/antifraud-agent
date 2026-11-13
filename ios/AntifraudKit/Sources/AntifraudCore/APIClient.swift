import Foundation

/// Ошибка обмена с сервером. Текст — из `{"error": …}` сервера, если он есть.
public enum APIError: Error, Sendable, Equatable, LocalizedError {
    /// 401: кода доступа нет или он неверный — экран просит код.
    case unauthorized(String)
    /// Сервер ответил ошибкой.
    case server(status: Int, message: String)
    /// Сервер не ответил: нет сети, неверный адрес, таймаут.
    case network(String)
    /// Ответ не той формы.
    case decoding(String)
    case invalidURL(String)

    public var status: Int? {
        switch self {
        case .unauthorized: 401
        case .server(let status, _): status
        default: nil
        }
    }

    public var errorDescription: String? {
        switch self {
        case .unauthorized(let m): m.isEmpty ? "Неверный код доступа" : m
        case .server(_, let m): m
        case .network(let m): m
        case .decoding(let m): "Сервер ответил непонятно: \(m)"
        case .invalidURL(let s): "Неверный адрес сервера: \(s)"
        }
    }

    /// Ошибка сети, после которой есть смысл повторить запрос.
    public var isTransient: Bool {
        switch self {
        case .network: true
        case .server(let status, _): status >= 500 || status == 408 || status == 429
        default: false
        }
    }
}

/// Транспорт HTTP: в приложении — URLSession, в тестах — подмена.
public protocol HTTPTransport: Sendable {
    func data(for request: URLRequest) async throws -> (Data, URLResponse)
    func upload(for request: URLRequest, fromFile file: URL) async throws -> (Data, URLResponse)
}

/// Настоящая сеть.
public struct URLSessionTransport: HTTPTransport {
    public let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        try await session.data(for: request, delegate: nil)
    }

    public func upload(for request: URLRequest, fromFile file: URL) async throws -> (Data, URLResponse) {
        try await session.upload(for: request, fromFile: file, delegate: nil)
    }
}

/// Куда слать куски звука: APIClient, в тестах — подмена.
public protocol AudioTransport: Sendable {
    func sendAudio(callID: String, seq: Int, data: Data) async throws
}

/// Адрес сервера и код доступа.
public struct ServerConfig: Sendable, Hashable, Codable {
    public var baseURL: URL
    public var token: String

    public init(baseURL: URL, token: String = "") {
        self.baseURL = baseURL
        self.token = token
    }

    public static let defaultPort = 8642

    /// Адрес, введённый руками: «192.168.1.5», «192.168.1.5:8642», «http://mac.local:8642/» → URL.
    /// Без схемы — http, без порта — 8642.
    public static func parseAddress(_ raw: String) -> URL? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        if !s.contains("://") { s = "http://" + s }
        guard var c = URLComponents(string: s), let scheme = c.scheme?.lowercased(), ["http", "https"].contains(scheme),
              let host = c.host, !host.isEmpty
        else { return nil }
        c.scheme = scheme
        if c.port == nil, !raw.contains("://") { c.port = defaultPort }
        c.path = ""
        c.query = nil
        c.fragment = nil
        return c.url
    }
}

/// Все запросы моста ui/server.py. Структура без состояния: безопасно передавать между задачами.
public struct APIClient: Sendable, AudioTransport {
    public var config: ServerConfig
    public var transport: HTTPTransport
    /// Таймаут обычных запросов, секунды.
    public var timeout: TimeInterval = 15

    public init(config: ServerConfig, transport: HTTPTransport = URLSessionTransport()) {
        self.config = config
        self.transport = transport
    }

    // MARK: - Адреса моста

    public func info() async throws -> ServerInfo { try await get("api/info") }
    public func health() async throws -> Health { try await get("api/health", timeout: 20) }
    public func sources() async throws -> Sources { try await get("api/sources") }
    public func runs() async throws -> [Run] { try await get("api/runs") }
    public func cases(limit: Int = 100) async throws -> [CallCase] {
        try await get("api/cases", query: [URLQueryItem(name: "limit", value: String(limit))])
    }
    public func number(_ phone: String) async throws -> NumberHistory { try await get("api/numbers/\(Self.pathEscape(phone))") }
    public func schemes() async throws -> [SchemeBrief] { try await get("api/schemes") }
    public func scheme(_ id: String) async throws -> Scheme { try await get("api/schemes/\(Self.pathEscape(id))") }
    public func callStatus(_ callID: String) async throws -> CallStatus { try await get("api/calls/\(Self.pathEscape(callID))") }

    /// POST /api/calls — 201 и состояние процесса; 409 — уже идёт другой звонок.
    public func startCall(_ body: StartCallRequest) async throws -> CallStatus {
        var req = try request("api/calls", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONCoding.encoder().encode(body)
        return try await decode(send(req))
    }

    /// POST /api/calls/<id>/stop — «Стоп»: агент дописывает отчёт (до 45 с), ответ приходит сразу.
    public func stop(_ callID: String) async throws -> CallStatus {
        var req = try request("api/calls/\(Self.pathEscape(callID))/stop", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data("{}".utf8)
        return try await decode(send(req))
    }

    /// POST /api/calls/<id>/audio?seq=N — кусок PCM s16le 16 кГц моно, ответ 204.
    public func sendAudio(callID: String, seq: Int, data: Data) async throws {
        var req = try request(
            "api/calls/\(Self.pathEscape(callID))/audio", method: "POST",
            query: [URLQueryItem(name: "seq", value: String(seq))]
        )
        req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        req.httpBody = data
        _ = try await send(req)
    }

    /// POST /api/upload?name=… — запись звонка с телефона.
    public func upload(name: String, fileURL: URL) async throws -> UploadResult {
        var req = try request("api/upload", method: "POST", query: [URLQueryItem(name: "name", value: name)], timeout: 600)
        req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await perform { try await transport.upload(for: req, fromFile: fileURL) }
        return try decode(try check(data, response))
    }

    /// Запрос потока событий GET /api/calls/<id>/events.
    public func eventsRequest(callID: String, lastEventID: String?) throws -> URLRequest {
        // Таймаут — на тишину в потоке: сервер шлёт heartbeat раз в 15 с.
        var req = try request("api/calls/\(Self.pathEscape(callID))/events", timeout: 45)
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        if let lastEventID { req.setValue(lastEventID, forHTTPHeaderField: "Last-Event-ID") }
        return req
    }

    // MARK: - Общее

    public func request(
        _ path: String, method: String = "GET", query: [URLQueryItem] = [], timeout: TimeInterval? = nil
    ) throws -> URLRequest {
        let base = config.baseURL.absoluteString.hasSuffix("/") ? config.baseURL.absoluteString : config.baseURL.absoluteString + "/"
        guard var c = URLComponents(string: base + path) else { throw APIError.invalidURL(base + path) }
        if !query.isEmpty {
            c.queryItems = query
            // «+» в значении запроса сервер (parse_qs) прочитал бы как пробел.
            c.percentEncodedQuery = c.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        }
        guard let url = c.url else { throw APIError.invalidURL(base + path) }
        var req = URLRequest(url: url, timeoutInterval: timeout ?? self.timeout)
        req.httpMethod = method
        if !config.token.isEmpty { req.setValue("Bearer \(config.token)", forHTTPHeaderField: "Authorization") }
        return req
    }

    /// Часть пути: всё, кроме букв, цифр и «-._~», — в %XX (как encodeURIComponent).
    public static func pathEscape(_ s: String) -> String {
        var allowed = CharacterSet.alphanumerics.intersection(CharacterSet(charactersIn: Unicode.Scalar(0)..<Unicode.Scalar(128)))
        allowed.insert(charactersIn: "-._~")
        return s.addingPercentEncoding(withAllowedCharacters: allowed) ?? s
    }

    private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = [], timeout: TimeInterval? = nil) async throws -> T {
        try decode(try await send(request(path, query: query, timeout: timeout)))
    }

    private func send(_ req: URLRequest) async throws -> Data {
        let (data, response) = try await perform { try await transport.data(for: req) }
        return try check(data, response)
    }

    private func perform(_ op: () async throws -> (Data, URLResponse)) async throws -> (Data, URLResponse) {
        do {
            return try await op()
        } catch let e as APIError {
            throw e
        } catch is CancellationError {
            throw CancellationError()
        } catch let e as URLError where e.code == .cancelled {
            throw CancellationError()
        } catch {
            throw APIError.network(Self.networkMessage(error))
        }
    }

    /// Код ответа → ошибка с текстом сервера.
    func check(_ data: Data, _ response: URLResponse) throws -> Data {
        guard let http = response as? HTTPURLResponse else { throw APIError.network("Ответ не по HTTP") }
        if (200..<300).contains(http.statusCode) { return data }
        throw Self.error(status: http.statusCode, body: data)
    }

    public static func error(status: Int, body: Data) -> APIError {
        let message = (try? JSONDecoder().decode([String: JSONValue].self, from: body))?["error"]?.stringValue
            ?? "Сервер ответил \(status)"
        return status == 401 ? .unauthorized(message) : .server(status: status, message: message)
    }

    private func decode<T: Decodable>(_ data: Data) throws -> T {
        do {
            return try JSONCoding.decoder().decode(T.self, from: data)
        } catch {
            throw APIError.decoding(String(describing: error))
        }
    }

    static func networkMessage(_ error: Error) -> String {
        guard let e = error as? URLError else { return error.localizedDescription }
        switch e.code {
        case .notConnectedToInternet, .networkConnectionLost, .dataNotAllowed:
            return "Нет сети: телефон и компьютер должны быть в одной сети Wi-Fi"
        case .cannotFindHost, .dnsLookupFailed:
            return "Сервер не найден: проверьте адрес"
        case .cannotConnectToHost:
            return "Сервер не отвечает: запущен ли ui.server с --host 0.0.0.0?"
        case .timedOut:
            return "Сервер не ответил вовремя"
        case .appTransportSecurityRequiresSecureConnection:
            return "iOS запретила http-адрес: разрешены только адреса локальной сети"
        default:
            return e.localizedDescription
        }
    }
}
