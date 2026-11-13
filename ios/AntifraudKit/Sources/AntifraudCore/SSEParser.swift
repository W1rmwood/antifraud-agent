import Foundation

/// Одно сообщение Server-Sent Events.
public struct SSEMessage: Sendable, Equatable {
    public var id: String?
    public var event: String
    public var data: String

    public init(id: String? = nil, event: String = "message", data: String) {
        self.id = id
        self.event = event
        self.data = data
    }
}

/// Чистый разбор потока SSE (спецификация WHATWG): строки `id`, `event`, `data`, комментарии «:…»,
/// пустая строка — конец сообщения. Байты можно подавать кусками любого размера: строка, разрезанная
/// посередине (и даже посреди буквы UTF-8), склеивается.
///
/// Свои байты, а не `URLSession.AsyncBytes.lines`: пустые строки, которыми SSE отделяет сообщения,
/// по `lines` не приходят.
public struct SSEParser: Sendable {
    /// `id` последнего доставленного сообщения: его отправляют в Last-Event-ID при переподключении.
    /// Как в EventSource, обновляется только когда сообщение дошло целиком: `id` оборванного сообщения
    /// не считается, иначе сервер (он продолжает с Last-Event-ID + 1) не прислал бы его заново.
    public private(set) var lastEventID: String?
    private var idBuffer: String?

    private var buffer: [UInt8] = []
    private var afterCR = false
    private var event = ""
    private var data: [String] = []
    private var hasData = false
    private var messageID: String?

    public init(lastEventID: String? = nil) {
        self.lastEventID = lastEventID
        self.idBuffer = lastEventID
    }

    /// Подать очередные байты; вернуть сообщения, которые в них закончились.
    public mutating func feed(_ bytes: some Sequence<UInt8>) -> [SSEMessage] {
        var out: [SSEMessage] = []
        for b in bytes {
            if afterCR {
                afterCR = false
                if b == 0x0A { continue }  // CRLF — один перевод строки
            }
            if b == 0x0A || b == 0x0D {
                afterCR = b == 0x0D
                let line = String(decoding: buffer, as: UTF8.self)
                buffer.removeAll(keepingCapacity: true)
                if let m = processLine(line) { out.append(m) }
            } else {
                buffer.append(b)
            }
        }
        return out
    }

    public mutating func feed(_ text: String) -> [SSEMessage] {
        feed(Array(text.utf8))
    }

    /// Поток оборвался: недописанное сообщение по спецификации выбрасывается.
    public mutating func reset() {
        idBuffer = lastEventID
        buffer.removeAll()
        afterCR = false
        event = ""
        data = []
        hasData = false
        messageID = nil
    }

    private mutating func processLine(_ line: String) -> SSEMessage? {
        if line.isEmpty { return dispatch() }
        if line.hasPrefix(":") { return nil }  // комментарий: heartbeat сервера
        let field: Substring
        var value: Substring
        if let colon = line.firstIndex(of: ":") {
            field = line[..<colon]
            value = line[line.index(after: colon)...]
            if value.first == " " { value = value.dropFirst() }
        } else {
            field = Substring(line)
            value = ""
        }
        switch field {
        case "event": event = String(value)
        case "data":
            data.append(String(value))
            hasData = true
        case "id":
            if !value.contains("\0") {
                idBuffer = String(value)
                messageID = String(value)
            }
        default: break  // retry и неизвестные поля
        }
        return nil
    }

    private mutating func dispatch() -> SSEMessage? {
        defer {
            event = ""
            data = []
            hasData = false
            messageID = nil
        }
        lastEventID = idBuffer
        guard hasData else { return nil }
        return SSEMessage(id: messageID, event: event.isEmpty ? "message" : event, data: data.joined(separator: "\n"))
    }
}
