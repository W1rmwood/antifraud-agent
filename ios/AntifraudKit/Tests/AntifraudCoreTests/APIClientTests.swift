import Foundation
import Testing
@testable import AntifraudCore

/// Подменённый HTTP: запоминает запросы, отвечает заготовкой.
final class FakeHTTP: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var _requests: [URLRequest] = []
    var requests: [URLRequest] { lock.withLock { _requests } }
    let status: Int
    let body: Data

    init(status: Int = 200, json: String) {
        self.status = status
        self.body = Data(json.utf8)
    }

    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        lock.withLock { _requests.append(request) }
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: nil)!
        return (body, response)
    }

    func upload(for request: URLRequest, fromFile file: URL) async throws -> (Data, URLResponse) {
        try await data(for: request)
    }
}

struct FailingHTTP: HTTPTransport {
    let error: Error
    func data(for request: URLRequest) async throws -> (Data, URLResponse) { throw error }
    func upload(for request: URLRequest, fromFile file: URL) async throws -> (Data, URLResponse) { throw error }
}

let testConfig = ServerConfig(baseURL: URL(string: "http://192.168.1.5:8642")!, token: "123456")

@Suite("APIClient")
struct APIClientTests {
    @Test("Код доступа уходит в Authorization: Bearer")
    func bearer() async throws {
        let http = FakeHTTP(json: #"{"app":"antifraud","api":1,"auth":true}"#)
        let info = try await APIClient(config: testConfig, transport: http).info()
        #expect(info == ServerInfo(app: "antifraud", api: 1, auth: true))
        #expect(info.isAntifraud)
        let req = try #require(http.requests.first)
        #expect(req.url?.absoluteString == "http://192.168.1.5:8642/api/info")
        #expect(req.value(forHTTPHeaderField: "Authorization") == "Bearer 123456")
    }

    @Test("Без кода заголовка нет")
    func noToken() async throws {
        let http = FakeHTTP(json: #"{"app":"antifraud","api":1,"auth":false}"#)
        _ = try await APIClient(config: ServerConfig(baseURL: testConfig.baseURL), transport: http).info()
        #expect(http.requests.first?.value(forHTTPHeaderField: "Authorization") == nil)
    }

    @Test("Запуск remote-звонка: тело JSON без пустых полей")
    func startRemote() async throws {
        let http = FakeHTTP(status: 201, json: #"{"call_id":"ui-1","source":"remote","running":true,"stopping":false,"returncode":null,"error":""}"#)
        let status = try await APIClient(config: testConfig, transport: http).startCall(.remote(phone: "+79990000001"))
        #expect(status.callID == "ui-1")
        #expect(status.source == "remote")
        #expect(status.running)
        let req = try #require(http.requests.first)
        #expect(req.httpMethod == "POST")
        #expect(req.url?.path == "/api/calls")
        let body = try JSONSerialization.jsonObject(with: try #require(req.httpBody)) as? [String: Any]
        #expect(body?["source"] as? String == "remote")
        #expect(body?["phone"] as? String == "+79990000001")
        #expect(body?.keys.sorted() == ["phone", "source"])
    }

    @Test("Кусок звука: seq в запросе, octet-stream, тело как есть")
    func sendAudio() async throws {
        let http = FakeHTTP(status: 204, json: "")
        let pcm = Data([1, 0, 2, 0])
        try await APIClient(config: testConfig, transport: http).sendAudio(callID: "ui-1", seq: 7, data: pcm)
        let req = try #require(http.requests.first)
        #expect(req.url?.absoluteString == "http://192.168.1.5:8642/api/calls/ui-1/audio?seq=7")
        #expect(req.value(forHTTPHeaderField: "Content-Type") == "application/octet-stream")
        #expect(req.httpBody == pcm)
    }

    @Test("Номер в пути кодируется: «+» не превращается в пробел")
    func numberPath() async throws {
        let http = FakeHTTP(json: #"{"info":{"phone":"+79990000001","calls_count":0,"first_seen":null,"last_seen":null,"schemes":[],"tags":[]},"cases":[]}"#)
        let history = try await APIClient(config: testConfig, transport: http).number("+79990000001")
        #expect(history.info.callsCount == 0)
        #expect(http.requests.first?.url?.absoluteString == "http://192.168.1.5:8642/api/numbers/%2B79990000001")
    }

    @Test("Имя загружаемого файла: «+» в запросе кодируется")
    func uploadName() async throws {
        let http = FakeHTTP(status: 201, json: #"{"path":"data/uploads/x.wav","name":"a+b.wav","size":10}"#)
        let r = try await APIClient(config: testConfig, transport: http).upload(name: "a+b звонок.wav", fileURL: URL(fileURLWithPath: "/dev/null"))
        #expect(r.path == "data/uploads/x.wav")
        let query = http.requests.first?.url?.query(percentEncoded: true) ?? ""
        #expect(query.contains("a%2Bb"))
        #expect(!query.contains("+"))
    }

    @Test("Ошибка сервера — текст из {\"error\": …}")
    func serverError() async {
        let http = FakeHTTP(status: 409, json: #"{"error":"уже идёт звонок ui-0"}"#)
        await #expect(throws: APIError.server(status: 409, message: "уже идёт звонок ui-0")) {
            try await APIClient(config: testConfig, transport: http).startCall(.remote(phone: nil))
        }
    }

    @Test("401 — отдельный случай")
    func unauthorized() async {
        let http = FakeHTTP(status: 401, json: #"{"error":"нужен код доступа"}"#)
        await #expect(throws: APIError.unauthorized("нужен код доступа")) {
            try await APIClient(config: testConfig, transport: http).health()
        }
    }

    @Test("Ответ без JSON — код ответа в тексте")
    func plainError() async {
        let http = FakeHTTP(status: 502, json: "Bad Gateway")
        await #expect(throws: APIError.server(status: 502, message: "Сервер ответил 502")) {
            try await APIClient(config: testConfig, transport: http).sources()
        }
    }

    @Test("Нет сети — APIError.network с понятным текстом")
    func network() async {
        let client = APIClient(config: testConfig, transport: FailingHTTP(error: URLError(.cannotConnectToHost)))
        do {
            _ = try await client.info()
            Issue.record("ожидалась ошибка")
        } catch let e as APIError {
            guard case .network(let text) = e else { Issue.record("не network: \(e)"); return }
            #expect(text.contains("ui.server"))
            #expect(e.isTransient)
        } catch {
            Issue.record("не APIError: \(error)")
        }
    }

    @Test("Разбор ответов моста")
    func bridgeDecoding() throws {
        let d = JSONCoding.decoder()
        let sources = try d.decode(Sources.self, from: Data(#"""
        {"calls":[{"path":"data/calls/001.wav","title":"001","phone":"+79990000001","scheme_id":"safe-account","kind":"audio"}],
         "records":[{"path":"data/runs/demo.jsonl","title":"Демо"}],"busy":null}
        """#.utf8))
        #expect(sources.calls.first?.schemeID == "safe-account")
        #expect(sources.busy == nil)
        let runs = try d.decode([Run].self, from: Data(#"""
        [{"call_id":"ui-1","started_at":"2026-10-29T19:33:00.955160+00:00","last_at":"2026-10-29T19:34:00+00:00","phone":null,
          "running":false,"ended":true,"summary":null,"scheme":{"id":"x","name":"X","confidence":0.9},"warnings":2,"lines":5,"replay":false}]
        """#.utf8))
        #expect(runs.first?.scheme?.name == "X")
        let brief = try d.decode([SchemeBrief].self, from: Data(#"""
        [{"id":"a","name":"А","aliases":[],"category":"bank","legend":"банк","description":"…","stages":[{"n":1,"name":"Звонок"}]}]
        """#.utf8))
        #expect(brief.first?.category == .bank)
        let health = try d.decode(Health.self, from: Data(#"""
        {"model_ready":false,"checks":[{"key":"llm","label":"Модель qwen3:8b","ok":false,"detail":"Ollama не запущена","fix":"ollama serve"}]}
        """#.utf8))
        #expect(health.checks.first?.fix == "ollama serve")
    }
}

/// Подменённый SSE: соединения выдаются по очереди из заготовок.
final class FakeSSE: SSEConnector, @unchecked Sendable {
    enum Step { case status(Int, String), body([String]), fail }
    private let lock = NSLock()
    private var steps: [Step]
    private var _requests: [URLRequest] = []
    var requests: [URLRequest] { lock.withLock { _requests } }

    init(_ steps: [Step]) { self.steps = steps }

    func connect(_ request: URLRequest) async throws -> SSEConnection {
        let step: Step? = lock.withLock {
            _requests.append(request)
            return steps.isEmpty ? nil : steps.removeFirst()
        }
        switch step {
        case .status(let code, let body)?:
            return SSEConnection(status: code, chunks: AsyncThrowingStream { c in
                c.yield(Data(body.utf8))
                c.finish()
            })
        case .body(let chunks)?:
            return SSEConnection(status: 200, chunks: AsyncThrowingStream { c in
                for chunk in chunks { c.yield(Data(chunk.utf8)) }
                c.finish(throwing: URLError(.networkConnectionLost))
            })
        case .fail?, nil:
            throw URLError(.cannotConnectToHost)
        }
    }
}

@Suite("EventStream")
struct EventStreamTests {
    func event(_ seq: Int, _ type: String = "transcript") -> String {
        let payload = type == "call_ended" ? "{}" :
            #"{"call_id":"c","seq":\#(seq),"speaker":"unknown","text":"фраза \#(seq)","t_start":\#(seq),"t_end":\#(seq).5}"#
        return "id: \(seq)\nevent: agent\ndata: {\"call_id\":\"c\",\"seq\":\(seq),\"ts\":\"2026-10-29T19:33:0\(seq)Z\",\"type\":\"\(type)\",\"payload\":\(payload)}\n\n"
    }

    func makeStream(_ fake: FakeSSE) -> EventStream {
        var s = EventStream(client: APIClient(config: testConfig, transport: FakeHTTP(json: "{}")), connector: fake)
        s.sleep = { _ in }
        return s
    }

    @Test("Обрыв — переподключение с Last-Event-ID, после end поток заканчивается")
    func reconnect() async throws {
        let fake = FakeSSE([
            .body([event(0), event(1), "id: 2\nevent: agent\ndata: {\"обрыв"]),
            .fail,
            .body([event(2), event(3, "call_ended"), "event: process\ndata: {\"call_id\":\"c\",\"source\":\"remote\",\"running\":false,\"stopping\":false,\"returncode\":0,\"error\":\"\"}\n\n", "event: end\ndata: {}\n\n"]),
        ])
        var seqs: [Int?] = []
        var delays: [Double] = []
        var process: CallStatus?
        var ended = false
        for try await item in makeStream(fake).events(callID: "c") {
            switch item {
            case .agent(let e): seqs.append(e.seq)
            case .process(let s): process = s
            case .reconnecting(let after): delays.append(after)
            case .end: ended = true
            }
        }
        #expect(seqs == [0, 1, 2, 3])
        #expect(ended)
        #expect(process?.returncode == 0)
        #expect(delays == [0.5, 1.0])
        let ids = fake.requests.map { $0.value(forHTTPHeaderField: "Last-Event-ID") }
        // Событие 2 оборвалось на середине: продолжаем с 1, и сервер присылает 2 заново.
        #expect(ids == [nil, "1", "1"])
        #expect(fake.requests.first?.url?.path == "/api/calls/c/events")
        #expect(fake.requests.first?.value(forHTTPHeaderField: "Authorization") == "Bearer 123456")
    }

    @Test("404 — ошибка «лога звонка нет» без повторов")
    func notFound() async {
        let fake = FakeSSE([.status(404, #"{"error":"лога звонка нет"}"#), .body([event(0)])])
        await #expect(throws: APIError.server(status: 404, message: "лога звонка нет")) {
            for try await _ in makeStream(fake).events(callID: "c") {}
        }
        #expect(fake.requests.count == 1)
    }

    @Test("401 — без повторов")
    func unauthorized() async {
        let fake = FakeSSE([.status(401, #"{"error":"нужен код"}"#)])
        await #expect(throws: APIError.unauthorized("нужен код")) {
            for try await _ in makeStream(fake).events(callID: "c") {}
        }
    }

    @Test("Пауза между попытками растёт до 5 с")
    func backoff() async throws {
        let fake = FakeSSE(Array(repeating: .fail, count: 6) + [.body(["event: end\ndata: {}\n\n"])])
        var delays: [Double] = []
        for try await item in makeStream(fake).events(callID: "c") {
            if case .reconnecting(let d) = item { delays.append(d) }
        }
        #expect(delays == [0.5, 1, 2, 4, 5, 5])
    }
}
