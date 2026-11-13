import Foundation

/// Открытое соединение SSE: код ответа и байты тела кусками.
public struct SSEConnection: Sendable {
    public var status: Int
    public var chunks: AsyncThrowingStream<Data, Error>

    public init(status: Int, chunks: AsyncThrowingStream<Data, Error>) {
        self.status = status
        self.chunks = chunks
    }
}

/// Кто открывает соединение: URLSession или подмена в тестах.
public protocol SSEConnector: Sendable {
    func connect(_ request: URLRequest) async throws -> SSEConnection
}

/// SSE через `URLSession.bytes(for:)`. Байты отдаются кусками по строке.
public struct URLSessionSSEConnector: SSEConnector {
    public let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func connect(_ request: URLRequest) async throws -> SSEConnection {
        let (bytes, response) = try await session.bytes(for: request, delegate: nil)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        let chunks = AsyncThrowingStream<Data, Error> { continuation in
            let task = Task {
                do {
                    var line = Data()
                    for try await b in bytes {
                        line.append(b)
                        if b == 0x0A {
                            continuation.yield(line)
                            line.removeAll(keepingCapacity: true)
                        }
                    }
                    if !line.isEmpty { continuation.yield(line) }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
        return SSEConnection(status: status, chunks: chunks)
    }
}

/// Что приходит из потока событий звонка.
public enum StreamItem: Sendable {
    /// Событие агента из лога.
    case agent(AgentEvent)
    /// Состояние процесса звонка (идёт → завершаю → закончен).
    case process(CallStatus)
    /// Соединение оборвалось, через `after` секунд — новая попытка.
    case reconnecting(after: Double)
    /// Лог дочитан до конца: больше событий не будет.
    case end
}

/// Поток событий звонка GET /api/calls/<id>/events: переподключение с Last-Event-ID, пауза растёт до 5 с;
/// после `end` поток заканчивается; 404 — ошибка «лога звонка нет» без повторов.
public struct EventStream: Sendable {
    public var client: APIClient
    public var connector: SSEConnector
    public var initialDelay: Double = 0.5
    public var maxDelay: Double = 5
    public var sleep: @Sendable (Double) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }

    public init(client: APIClient, connector: SSEConnector = URLSessionSSEConnector()) {
        self.client = client
        self.connector = connector
    }

    public func events(callID: String, lastEventID: String? = nil) -> AsyncThrowingStream<StreamItem, Error> {
        let this = self
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    try await this.run(callID: callID, lastEventID: lastEventID) { continuation.yield($0) }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func run(callID: String, lastEventID: String?, yield: (StreamItem) -> Void) async throws {
        var lastID = lastEventID
        var delay = initialDelay
        let decoder = JSONCoding.decoder()
        while true {
            try Task.checkCancellation()
            var parser = SSEParser(lastEventID: lastID)
            do {
                let conn = try await connector.connect(client.eventsRequest(callID: callID, lastEventID: lastID))
                if conn.status != 200 {
                    var body = Data()
                    for try await chunk in conn.chunks { body.append(chunk) }
                    var error = APIClient.error(status: conn.status, body: body)
                    if conn.status == 404, case .server = error, body.isEmpty {
                        error = .server(status: 404, message: "лога звонка нет")
                    }
                    throw error
                }
                for try await chunk in conn.chunks {
                    for m in parser.feed(chunk) {
                        delay = initialDelay
                        switch m.event {
                        case "agent":
                            // Событие, которое не разобралось, пропускаем: остальной звонок важнее.
                            if let e = try? decoder.decode(AgentEvent.self, from: Data(m.data.utf8)) { yield(.agent(e)) }
                        case "process":
                            if let s = try? decoder.decode(CallStatus.self, from: Data(m.data.utf8)) { yield(.process(s)) }
                        case "end":
                            yield(.end)
                            return
                        default:
                            break
                        }
                    }
                    lastID = parser.lastEventID
                }
                lastID = parser.lastEventID
            } catch let e as APIError where !e.isTransient {
                throw e
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                if Task.isCancelled { throw CancellationError() }
                lastID = parser.lastEventID
            }
            // Соединение закрылось без end (обрыв сети, сервер перезапустился) — продолжаем с места обрыва.
            yield(.reconnecting(after: delay))
            try await sleep(delay)
            delay = min(delay * 2, maxDelay)
        }
    }
}
