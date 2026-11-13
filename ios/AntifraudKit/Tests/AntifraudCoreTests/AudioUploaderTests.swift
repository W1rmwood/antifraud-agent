import Foundation
import Testing
@testable import AntifraudCore

/// Подменённый транспорт: запоминает попытки, отвечает по сценарию.
actor FakeAudioTransport: AudioTransport {
    enum Reply: Sendable { case ok, fail(APIError) }

    private(set) var attempts: [Int] = []
    private(set) var accepted: [Int] = []
    private(set) var bytes: [Int: Int] = [:]
    /// Ответы по номеру seq: сколько первых попыток провалить и чем.
    private var script: [Int: [Reply]] = [:]
    /// Пока true — каждая попытка падает с ошибкой сети.
    var offline = false

    func setOffline(_ value: Bool) { offline = value }

    func script(seq: Int, _ replies: [Reply]) { script[seq] = replies }

    func sendAudio(callID: String, seq: Int, data: Data) async throws {
        attempts.append(seq)
        await Task.yield()
        if offline { throw APIError.network("нет сети") }
        if var replies = script[seq], !replies.isEmpty {
            let reply = replies.removeFirst()
            script[seq] = replies
            if case .fail(let e) = reply { throw e }
        }
        accepted.append(seq)
        bytes[seq] = data.count
    }
}

/// Ждать условия, не дольше пары секунд.
func eventually(_ condition: @Sendable () async -> Bool) async -> Bool {
    for _ in 0..<400 {
        if await condition() { return true }
        try? await Task.sleep(for: .milliseconds(5))
    }
    return await condition()
}

private let chunk = Data(repeating: 1, count: 8000)  // 250 мс звука

@Suite("AudioUploader")
struct AudioUploaderTests {
    func makeUploader(_ t: FakeAudioTransport, maxSeconds: Double = 60) -> AudioUploader {
        AudioUploader(callID: "c1", transport: t, maxQueuedSeconds: maxSeconds, retryDelay: 0, sleep: { _ in await Task.yield() })
    }

    @Test("Куски уходят строго по порядку, seq растёт с 0")
    func order() async {
        let t = FakeAudioTransport()
        let u = makeUploader(t)
        for _ in 0..<20 { await u.enqueue(chunk) }
        #expect(await u.drain(timeout: 2))
        #expect(await t.accepted == Array(0..<20))
        #expect(await t.attempts == Array(0..<20))
        let status = await u.status
        #expect(status.state == .idle)
        #expect(status.sentChunks == 20)
        #expect(status.lostSeconds == 0)
    }

    @Test("Ошибка сети — повтор того же seq, порядок не нарушается")
    func retry() async {
        let t = FakeAudioTransport()
        await t.script(seq: 1, [.fail(.network("обрыв")), .fail(.server(status: 503, message: "занят"))])
        let u = makeUploader(t)
        for _ in 0..<4 { await u.enqueue(chunk) }
        #expect(await u.drain(timeout: 2))
        #expect(await t.attempts == [0, 1, 1, 1, 2, 3])
        #expect(await t.accepted == [0, 1, 2, 3])
    }

    @Test("Состояние retrying, пока сети нет")
    func retryingState() async {
        let t = FakeAudioTransport()
        await t.setOffline(true)
        let u = makeUploader(t)
        await u.enqueue(chunk)
        #expect(await eventually { if case .retrying = await u.status.state { true } else { false } })
        await t.setOffline(false)
        #expect(await u.drain(timeout: 2))
        #expect(await t.accepted == [0])
    }

    @Test("Переполнение очереди: выбрасываются самые старые куски, их seq пропускается")
    func overflow() async {
        let t = FakeAudioTransport()
        await t.setOffline(true)
        // Очередь — 1 секунда, то есть 4 куска по 250 мс.
        let u = makeUploader(t, maxSeconds: 1)
        await u.enqueue(chunk)
        // Кусок 0 уже в отправке (повторяется), остальные копятся в очереди.
        #expect(await eventually { await !t.attempts.isEmpty })
        for _ in 1..<10 { await u.enqueue(chunk) }
        let during = await u.status
        #expect(during.lostSeconds == 1.25)  // выброшены 1…5
        await t.setOffline(false)
        #expect(await u.drain(timeout: 2))
        #expect(await t.accepted == [0, 6, 7, 8, 9])
        #expect(await u.status.lostSeconds == 1.25)
    }

    @Test("409 — звонок закончился: отправка прекращается, новые куски не принимаются")
    func stopOn409() async {
        let t = FakeAudioTransport()
        await t.script(seq: 2, [.fail(.server(status: 409, message: "звонок уже закончился"))])
        let u = makeUploader(t)
        for _ in 0..<5 { await u.enqueue(chunk) }
        #expect(await eventually { await u.isStopped })
        await u.enqueue(chunk)
        try? await Task.sleep(for: .milliseconds(20))
        #expect(await t.accepted == [0, 1])
        #expect(await t.attempts == [0, 1, 2])
        #expect(await u.status.state == .stopped(.callEnded))
    }

    @Test("404 и 401 тоже останавливают отправку")
    func stopOn404And401() async {
        let t1 = FakeAudioTransport()
        await t1.script(seq: 0, [.fail(.server(status: 404, message: "звонок не найден"))])
        let u1 = makeUploader(t1)
        await u1.enqueue(chunk)
        #expect(await eventually { await u1.status.state == .stopped(.callEnded) })

        let t2 = FakeAudioTransport()
        await t2.script(seq: 0, [.fail(.unauthorized("неверный код"))])
        let u2 = makeUploader(t2)
        await u2.enqueue(chunk)
        #expect(await eventually { await u2.status.state == .stopped(.unauthorized) })
    }

    @Test("400 — кусок не повторяется, считается потерянным, остальные уходят")
    func badChunkSkipped() async {
        let t = FakeAudioTransport()
        await t.script(seq: 1, [.fail(.server(status: 400, message: "нечётное число байт"))])
        let u = makeUploader(t)
        for _ in 0..<3 { await u.enqueue(chunk) }
        #expect(await u.drain(timeout: 2))
        #expect(await t.accepted == [0, 2])
        #expect(await u.status.lostSeconds == 0.25)
    }

    @Test("cancel выбрасывает очередь")
    func cancel() async {
        let t = FakeAudioTransport()
        await t.setOffline(true)
        let u = makeUploader(t)
        for _ in 0..<3 { await u.enqueue(chunk) }
        await u.cancel()
        #expect(await u.status.state == .stopped(.cancelled))
        #expect(await u.isDrained)
        await t.setOffline(false)
        try? await Task.sleep(for: .milliseconds(20))
        // Запрос, который уже ушёл, может дойти; остальные куски — нет, и повторов больше не будет.
        #expect(await t.accepted.allSatisfy { $0 == 0 })
        #expect(await !t.attempts.contains(1))
        let attempts = await t.attempts.count
        try? await Task.sleep(for: .milliseconds(20))
        #expect(await t.attempts.count == attempts)
    }

    @Test("Поток состояний заканчивается после остановки")
    func statusStreamFinishes() async {
        let t = FakeAudioTransport()
        await t.script(seq: 0, [.fail(.server(status: 409, message: ""))])
        let u = makeUploader(t)
        await u.enqueue(chunk)
        var last: AudioUploader.Status?
        for await s in u.statusUpdates { last = s }
        #expect(last?.state == .stopped(.callEnded))
    }
}
