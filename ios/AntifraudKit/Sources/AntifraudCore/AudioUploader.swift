import Foundation

/// Очередь кусков звука для POST /api/calls/<id>/audio?seq=N.
///
/// Шлёт строго по порядку, по одному запросу за раз; ошибка сети — повтор того же `seq` после паузы.
/// В очереди не больше `maxQueuedSeconds` звука: при переполнении выбрасываются самые старые куски
/// (их `seq` пропускается — сервер это допускает). 404 и 409 — звонок закончился, отправка прекращается.
public actor AudioUploader {
    public enum State: Sendable, Equatable {
        /// Очередь пуста, всё отправлено (или ещё ничего не было).
        case idle
        case sending
        /// Последняя попытка не удалась, повторяем; текст — причина.
        case retrying(String)
        case stopped(StopReason)
    }

    public enum StopReason: Sendable, Equatable {
        /// 404 / 409: звонка на сервере больше нет или он уже закончился.
        case callEnded
        /// 401: код доступа перестал подходить.
        case unauthorized
        /// Остановлено приложением.
        case cancelled
    }

    public struct Status: Sendable, Equatable {
        public var state: State
        /// Сколько секунд звука потеряно: выброшено из переполненной очереди или отвергнуто сервером.
        public var lostSeconds: Double
        /// Сколько секунд звука ждёт отправки.
        public var queuedSeconds: Double
        /// Сколько кусков принято сервером.
        public var sentChunks: Int
    }

    /// 16 000 Гц × 2 байта × 1 канал.
    public static let bytesPerSecond = 32_000

    public let callID: String
    private let transport: AudioTransport
    private let maxQueuedBytes: Int
    private let retryDelay: Double
    private let sleep: @Sendable (Double) async throws -> Void

    private var queue: [(seq: Int, data: Data)] = []
    private var queuedBytes = 0
    private var inFlight: (seq: Int, data: Data)?
    private var nextSeq = 0
    private var lostBytes = 0
    private var sent = 0
    private var pumping = false
    private var state: State = .idle {
        didSet { if state != oldValue { publish() } }
    }

    /// Изменения состояния: для индикатора отправки на экране звонка.
    public nonisolated let statusUpdates: AsyncStream<Status>
    private let statusContinuation: AsyncStream<Status>.Continuation

    public init(
        callID: String,
        transport: AudioTransport,
        maxQueuedSeconds: Double = 60,
        retryDelay: Double = 1,
        sleep: @escaping @Sendable (Double) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }
    ) {
        self.callID = callID
        self.transport = transport
        self.maxQueuedBytes = Int(maxQueuedSeconds * Double(Self.bytesPerSecond))
        self.retryDelay = retryDelay
        self.sleep = sleep
        (statusUpdates, statusContinuation) = AsyncStream.makeStream(of: Status.self, bufferingPolicy: .bufferingNewest(1))
    }

    public var status: Status {
        Status(
            state: state,
            lostSeconds: Double(lostBytes) / Double(Self.bytesPerSecond),
            queuedSeconds: Double(queuedBytes + (inFlight?.data.count ?? 0)) / Double(Self.bytesPerSecond),
            sentChunks: sent
        )
    }

    public var isStopped: Bool {
        if case .stopped = state { return true }
        return false
    }

    /// Всё отправлено (или отправлять больше некуда).
    public var isDrained: Bool { isStopped || (queue.isEmpty && inFlight == nil) }

    /// Принять кусок звука. Номер `seq` присваивается здесь, по порядку поступления.
    public func enqueue(_ data: Data) {
        guard !isStopped, !data.isEmpty else { return }
        queue.append((nextSeq, data))
        nextSeq += 1
        queuedBytes += data.count
        while queuedBytes > maxQueuedBytes, !queue.isEmpty {
            let dropped = queue.removeFirst()
            queuedBytes -= dropped.data.count
            lostBytes += dropped.data.count
        }
        publish()
        if !pumping {
            pumping = true
            Task { await self.pump() }
        }
    }

    /// Ждать, пока очередь опустеет, не дольше `timeout` секунд. true — всё отправлено.
    @discardableResult
    public func drain(timeout: Double) async -> Bool {
        let deadline = ContinuousClock.now + .seconds(timeout)
        while !isDrained, ContinuousClock.now < deadline {
            try? await Task.sleep(for: .milliseconds(50))
        }
        return queue.isEmpty && inFlight == nil
    }

    /// Прекратить отправку: очередь выбрасывается.
    public func cancel() {
        stop(.cancelled)
    }

    private func stop(_ reason: StopReason) {
        guard !isStopped else { return }
        queue.removeAll()
        queuedBytes = 0
        inFlight = nil
        state = .stopped(reason)
        statusContinuation.yield(status)
        statusContinuation.finish()
    }

    private func publish() {
        statusContinuation.yield(status)
    }

    private func pump() async {
        defer { pumping = false }
        while !isStopped {
            if inFlight == nil {
                guard !queue.isEmpty else { break }
                let next = queue.removeFirst()
                queuedBytes -= next.data.count
                inFlight = next
            }
            guard let chunk = inFlight else { break }
            if case .retrying = state {} else { state = .sending }
            do {
                try await transport.sendAudio(callID: callID, seq: chunk.seq, data: chunk.data)
                guard !isStopped else { break }
                inFlight = nil
                sent += 1
                state = .sending
                publish()
            } catch let e as APIError where e.status == 404 || e.status == 409 {
                stop(.callEnded)
            } catch let e as APIError where e.status == 401 {
                stop(.unauthorized)
            } catch let e as APIError where e.status == 400 || e.status == 413 {
                // Кусок, который сервер не примет никогда: повторять бессмысленно, считаем потерянным.
                if inFlight?.seq == chunk.seq {
                    inFlight = nil
                    lostBytes += chunk.data.count
                }
                publish()
            } catch {
                guard !isStopped else { break }
                state = .retrying((error as? LocalizedError)?.errorDescription ?? error.localizedDescription)
                try? await sleep(retryDelay)
            }
        }
        if !isStopped, queue.isEmpty, inFlight == nil { state = .idle }
    }
}
