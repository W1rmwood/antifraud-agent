import Foundation
import Testing
@testable import AntifraudCore

@Suite("SSEParser")
struct SSEParserTests {
    @Test("Поток как у ui/server.py: id, event, data")
    func serverFormat() {
        var p = SSEParser()
        let out = p.feed("id: 0\nevent: agent\ndata: {\"a\":1}\n\nevent: process\ndata: {\"running\":true}\n\n")
        #expect(out == [
            SSEMessage(id: "0", event: "agent", data: "{\"a\":1}"),
            SSEMessage(id: nil, event: "process", data: "{\"running\":true}"),
        ])
        #expect(p.lastEventID == "0")
    }

    @Test("Многострочный data склеивается через перевод строки")
    func multilineData() {
        var p = SSEParser()
        let out = p.feed("data: первая\ndata: вторая\ndata:третья\n\n")
        #expect(out == [SSEMessage(data: "первая\nвторая\nтретья")])
    }

    @Test("Комментарий-heartbeat ничего не выдаёт и не ломает следующее сообщение")
    func heartbeat() {
        var p = SSEParser()
        #expect(p.feed(": heartbeat\n\n").isEmpty)
        #expect(p.feed("event: end\n: ещё комментарий\ndata: {}\n\n") == [SSEMessage(event: "end", data: "{}")])
    }

    @Test("id засчитывается только у дошедшего сообщения и переживает сообщения без id")
    func lastEventID() {
        var p = SSEParser(lastEventID: "4")
        #expect(p.lastEventID == "4")
        _ = p.feed("id: 5\nevent: agent\ndata: x\n")
        #expect(p.lastEventID == "4")
        _ = p.feed("\n")
        _ = p.feed("event: process\ndata: y\n\n")
        #expect(p.lastEventID == "5")
    }

    @Test("Обрыв посреди события: недописанное не выдаётся, строка склеивается из кусков")
    func brokenMidEvent() {
        var p = SSEParser()
        #expect(p.feed("id: 7\nevent: ag").isEmpty)
        #expect(p.feed("ent\ndata: {\"x\"").isEmpty)
        #expect(p.feed(":1}\n") .isEmpty)
        #expect(p.feed("\n") == [SSEMessage(id: "7", event: "agent", data: "{\"x\":1}")])

        // Соединение оборвалось до пустой строки — сообщение выбрасывается.
        _ = p.feed("id: 8\nevent: agent\ndata: {\"half\"")
        p.reset()
        #expect(p.feed("event: end\ndata: {}\n\n") == [SSEMessage(event: "end", data: "{}")])
        // id оборванного сообщения не засчитан: сервер пришлёт это событие заново.
        #expect(p.lastEventID == "7")
    }

    @Test("Буква UTF-8, разрезанная между кусками")
    func splitUTF8() {
        var p = SSEParser()
        let bytes = Array("data: ёж\n\n".utf8)
        let cut = 7  // посреди «ё»
        #expect(p.feed(bytes[..<cut]).isEmpty)
        #expect(p.feed(bytes[cut...]) == [SSEMessage(data: "ёж")])
    }

    @Test("CRLF и CR как переводы строк")
    func lineEndings() {
        var p = SSEParser()
        #expect(p.feed("data: a\r\n\r\ndata: b\r\rdata: c\n\n") == [
            SSEMessage(data: "a"), SSEMessage(data: "b"), SSEMessage(data: "c"),
        ])
        // CRLF, разрезанный между кусками, — тоже один перевод строки.
        #expect(p.feed("data: d\r").isEmpty)
        #expect(p.feed("\n\r\n") == [SSEMessage(data: "d")])
    }

    @Test("Пустая строка без data ничего не выдаёт; поле без двоеточия")
    func emptyAndBareField() {
        var p = SSEParser()
        #expect(p.feed("event: x\n\n").isEmpty)
        #expect(p.feed("data\n\n") == [SSEMessage(data: "")])
    }
}
