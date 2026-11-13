import AntifraudAudio
import AntifraudCore
import Foundation
import Observation

/// Один звонок на экране: идущий (живой с микрофона, файл, запись) или открытый из истории.
///
/// Живой звонок: idle → starting (POST /api/calls) → live (микрофон + отправка + SSE)
/// → stopping (POST …/stop, микрофон выключен, SSE дочитывается до call_ended) → finished.
@MainActor
@Observable
public final class CallSession {
    public enum Phase: Equatable, Sendable {
        case idle, starting, live, stopping, finished
        case failed(String)
    }

    public enum Kind: Equatable, Sendable {
        /// Звук с микрофона телефона (source = remote).
        case remote
        /// Звонок, запущенный на сервере: тестовый файл, загруженная запись, проигрывание.
        case server
        /// Уже идущий или законченный звонок: только поток событий.
        case viewer
    }

    public let kind: Kind
    public private(set) var phase: Phase = .idle
    public private(set) var callID: String?
    public private(set) var state = CallState()
    public private(set) var process: CallStatus?
    /// Пришёл end: лог дочитан.
    public private(set) var streamDone = false
    /// Лога звонка нет (404 и ни одного события).
    public private(set) var missing = false
    public private(set) var streamError: String?
    public private(set) var reconnecting = false
    public private(set) var unauthorized = false
    public private(set) var stopError: String?

    // Микрофон и отправка (только remote).
    public private(set) var mic: MicState = .idle
    public private(set) var level: Float = 0
    public private(set) var upload: AudioUploader.Status?

    private let client: APIClient
    private var micCapture: MicCapture?
    private var uploader: AudioUploader?
    /// Поток событий: отменяется и по close(), и когда сеанс больше никому не нужен (экран закрыт).
    private let streamTask = TaskBox()
    private var micTasks: [Task<Void, Never>] = []
    private var forwardTask: Task<Void, Never>?
    private var lastHaptic = Date.distantPast
    private var seenAlarms = 0
    private var seenScheme: String?

    public init(client: APIClient, kind: Kind) {
        self.client = client
        self.kind = kind
    }

    // MARK: - Производное состояние, как в CallPage.jsx

    /// Процесс ещё идёт (пока его состояние неизвестно — пока поток не закончился).
    public var running: Bool { process?.running ?? !streamDone }
    public var live: Bool { running && !state.ended && phase != .finished && !isFailed }
    public var crashed: Bool {
        guard let p = process else { return false }
        return !p.running && p.returncode != 0 && !state.ended
    }
    public var isStopping: Bool { phase == .stopping || process?.stopping == true }
    /// Проигранная запись, а не звонок через модель.
    public var replay: Bool { process?.source == "replay" || state.summaryFromOtherCall }
    public var isFailed: Bool { if case .failed = phase { true } else { false } }
    /// Агент упал, не сказав ни фразы.
    public var deadOnStart: Bool { crashed && state.lines.isEmpty }
    /// Звонок можно остановить с этого телефона.
    public var canStop: Bool { live && process != nil }

    // MARK: - Запуск

    /// Запустить звонок на сервере и, для remote, микрофон.
    public func start(_ request: StartCallRequest) async {
        guard phase == .idle else { return }
        phase = .starting
        let status: CallStatus
        do {
            status = try await client.startCall(request)
        } catch {
            unauthorized = (error as? APIError).map { if case .unauthorized = $0 { true } else { false } } ?? false
            phase = .failed(Self.message(error))
            return
        }
        callID = status.callID
        process = status
        phase = .live
        openStream(status.callID)
        if kind == .remote { await startMic(callID: status.callID) }
    }

    /// Открыть уже идущий или законченный звонок.
    public func attach(callID: String) {
        guard phase == .idle else { return }
        self.callID = callID
        phase = .live
        openStream(callID)
    }

    /// «Закончить»: микрофон выключается, остаток звука досылается, сервер дописывает отчёт.
    public func stop() async {
        guard let callID, phase == .live || phase == .starting else { return }
        phase = .stopping
        if kind == .remote { await finishAudio() }
        do {
            process = try await client.stop(callID)
        } catch let e as APIError where e.status == 404 {
            // Звонка на сервере уже нет — дочитываем поток.
        } catch {
            stopError = Self.message(error)
            if !state.ended { phase = .live }
        }
    }

    /// Экран закрыт: всё отключить. Звонок на сервере не останавливается.
    public func close() {
        streamTask.cancel()
        stopMic()
        Task { [uploader] in await uploader?.cancel() }
    }

    // MARK: - Поток событий

    private func openStream(_ callID: String) {
        let stream = EventStream(client: client).events(callID: callID)
        streamTask.task = Task { [weak self] in
            do {
                for try await item in stream {
                    guard let self else { return }
                    self.handle(item)
                }
            } catch is CancellationError {
                return
            } catch {
                self?.streamFailed(error)
            }
            self?.streamFinished()
        }
    }

    private func handle(_ item: StreamItem) {
        switch item {
        case .agent(let e):
            reconnecting = false
            state.apply(e)
            notifyIfAlarming()
            if state.ended { finish() }
        case .process(let s):
            reconnecting = false
            process = s
            if !s.running, !state.ended, s.returncode != 0, phase == .live || phase == .stopping {
                // Агент упал: микрофон больше не нужен.
                stopMic()
            }
        case .reconnecting:
            reconnecting = true
        case .end:
            streamDone = true
        }
    }

    private func streamFailed(_ error: Error) {
        if let e = error as? APIError {
            if case .unauthorized = e { unauthorized = true }
            if e.status == 404, state.eventCount == 0 {
                missing = true
                return
            }
        }
        streamError = Self.message(error)
    }

    private func streamFinished() {
        streamDone = true
        reconnecting = false
        finish()
    }

    private func finish() {
        guard phase != .finished else { return }
        stopMic()
        if case .failed = phase { return }
        phase = .finished
    }

    /// Вибрация на новую плашку и на объявление схемы — только пока звонок идёт и не чаще раза в секунду
    /// (законченный звонок приходит сотней событий разом).
    private func notifyIfAlarming() {
        let alarms = state.alarms.count
        let scheme = state.scheme?.schemeID
        defer {
            seenAlarms = alarms
            seenScheme = scheme
        }
        // Состояние процесса приходит после событий лога: у открытого из истории звонка его ещё нет.
        guard process?.running ?? (kind != .viewer) else { return }
        guard alarms > seenAlarms || (scheme != nil && scheme != seenScheme) else { return }
        guard Date().timeIntervalSince(lastHaptic) > 1 else { return }
        lastHaptic = Date()
        Platform.warningHaptic()
    }

    // MARK: - Микрофон и отправка

    private func startMic(callID: String) async {
        let capture = MicCapture()
        let up = AudioUploader(callID: callID, transport: client)
        micCapture = capture
        uploader = up
        micTasks.append(Task { [weak self] in
            for await s in capture.states { self?.mic = s }
        })
        micTasks.append(Task { [weak self] in
            for await l in capture.levels { self?.level = l }
        })
        micTasks.append(Task { [weak self] in
            for await s in up.statusUpdates {
                self?.upload = s
                if s.state == .stopped(.callEnded) { self?.stopMic() }
                if s.state == .stopped(.unauthorized) { self?.unauthorized = true }
            }
        })
        forwardTask = Task {
            for await chunk in capture.chunks { await up.enqueue(chunk) }
        }
        Platform.keepScreenOn(true)
        let result = await capture.start()
        mic = result
        switch result {
        case .denied:
            stopError = "Нет доступа к микрофону. Разрешите его: Настройки → Антимошенник → Микрофон."
            await stop()
        case .failed(let text):
            stopError = text
            await stop()
        default:
            break
        }
    }

    /// Остановить микрофон и дослать остаток звука (не дольше нескольких секунд).
    private func finishAudio() async {
        guard let capture = micCapture else { return }
        capture.stop()
        await forwardTask?.value
        await uploader?.drain(timeout: 3)
        stopMic()
    }

    private func stopMic() {
        micCapture?.stop()
        micCapture = nil
        forwardTask?.cancel()
        forwardTask = nil
        for t in micTasks { t.cancel() }
        micTasks.removeAll()
        level = 0
        if kind == .remote { Platform.keepScreenOn(false) }
    }

    static func message(_ error: Error) -> String {
        (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    }
}

/// Задача, которая отменяется вместе с владельцем.
final class TaskBox: @unchecked Sendable {
    private let lock = NSLock()
    private var _task: Task<Void, Never>?

    var task: Task<Void, Never>? {
        get { lock.withLock { _task } }
        set {
            let old = lock.withLock { () -> Task<Void, Never>? in
                defer { _task = newValue }
                return _task
            }
            old?.cancel()
        }
    }

    func cancel() {
        task = nil
    }

    deinit {
        _task?.cancel()
    }
}
