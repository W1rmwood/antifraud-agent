import AVFoundation
import Foundation

/// Состояние записи с микрофона.
public enum MicState: Sendable, Equatable {
    case idle
    case running
    /// Запись прервала система (входящий звонок на этот же iPhone, Siri); после прерывания возобновится сама.
    case interrupted
    /// Нет разрешения на микрофон.
    case denied
    case failed(String)
}

/// Микрофон → PCM 16 кГц, моно, Int16 little-endian → куски ровно по 250 мс (8000 байт).
///
/// `AVAudioEngine` с tap на `inputNode`, `AVAudioConverter` в нужный формат. Настройка `AVAudioSession`
/// и прерывания — только на iOS. Управление движком идёт через последовательную очередь, разбор звука —
/// в потоке tap; общий буфер нарезки защищён замком.
public final class MicCapture: @unchecked Sendable {
    public static let sampleRate: Double = 16_000
    public static let chunkBytes = 8_000

    /// Куски звука по 250 мс; последний при остановке — какой получился. Заканчивается после stop().
    public let chunks: AsyncStream<Data>
    /// Изменения состояния.
    public let states: AsyncStream<MicState>
    /// Громкость каждого куска 0…1 — для индикатора записи.
    public let levels: AsyncStream<Float>

    private let chunkContinuation: AsyncStream<Data>.Continuation
    private let stateContinuation: AsyncStream<MicState>.Continuation
    private let levelContinuation: AsyncStream<Float>.Continuation

    private let control = DispatchQueue(label: "antifraud.mic.control")
    private let lock = NSLock()
    private let engine = AVAudioEngine()
    // Ниже — только под lock.
    private var chunker = PCMChunker(chunkSize: chunkBytes)
    private var converter: AVAudioConverter?
    private var currentState: MicState = .idle
    private var finished = false
    private var observers: [NSObjectProtocol] = []

    public init() {
        (chunks, chunkContinuation) = AsyncStream.makeStream(of: Data.self, bufferingPolicy: .unbounded)
        (states, stateContinuation) = AsyncStream.makeStream(of: MicState.self, bufferingPolicy: .bufferingNewest(8))
        (levels, levelContinuation) = AsyncStream.makeStream(of: Float.self, bufferingPolicy: .bufferingNewest(1))
    }

    deinit {
        for o in observers { NotificationCenter.default.removeObserver(o) }
    }

    public var state: MicState { lock.withLock { currentState } }

    /// Спросить разрешение, настроить сессию и начать запись. Возвращает итоговое состояние.
    @discardableResult
    public func start() async -> MicState {
        guard await AVAudioApplication.requestRecordPermission() else {
            setState(.denied)
            return .denied
        }
        let result: MicState = await withCheckedContinuation { continuation in
            control.async {
                // stop() успели вызвать, пока система спрашивала разрешение: включать нечего.
                guard !self.lock.withLock({ self.finished }) else {
                    continuation.resume(returning: .idle)
                    return
                }
                do {
                    #if os(iOS)
                    try Self.activateSession()
                    #endif
                    try self.startEngine()
                    continuation.resume(returning: .running)
                } catch {
                    continuation.resume(returning: .failed(Self.describe(error)))
                }
            }
        }
        setState(result)
        if result == .running { observe() }
        return result
    }

    /// Остановить запись: последний неполный кусок отдаётся, потоки заканчиваются.
    public func stop() {
        control.sync {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
            #if os(iOS)
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            #endif
        }
        let (tail, alreadyFinished): (Data?, Bool) = lock.withLock {
            defer { finished = true }
            return (chunker.flush(), finished)
        }
        guard !alreadyFinished else { return }
        if let tail { chunkContinuation.yield(tail) }
        setState(.idle)
        chunkContinuation.finish()
        stateContinuation.finish()
        levelContinuation.finish()
        let list: [NSObjectProtocol] = lock.withLock {
            defer { observers.removeAll() }
            return observers
        }
        for o in list { NotificationCenter.default.removeObserver(o) }
    }

    // MARK: - Движок

    private func startEngine() throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw MicError("Микрофон недоступен")
        }
        guard let target = AVAudioFormat(
            commonFormat: .pcmFormatInt16, sampleRate: Self.sampleRate, channels: 1, interleaved: true
        ), let conv = AVAudioConverter(from: format, to: target) else {
            throw MicError("Не удалось настроить преобразование звука")
        }
        lock.withLock { converter = conv }
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
            self?.process(buffer)
        }
        engine.prepare()
        try engine.start()
    }

    /// Перезапуск после прерывания или смены устройства: формат входа мог измениться.
    private func restart() {
        control.async {
            guard !self.lock.withLock({ self.finished }) else { return }
            self.engine.inputNode.removeTap(onBus: 0)
            self.engine.stop()
            do {
                #if os(iOS)
                try Self.activateSession()
                #endif
                try self.startEngine()
                self.setState(.running)
            } catch {
                self.setState(.failed(Self.describe(error)))
            }
        }
    }

    /// Поток tap: преобразовать буфер в 16 кГц Int16 и нарезать.
    private func process(_ buffer: AVAudioPCMBuffer) {
        guard let converter = lock.withLock({ converter }), buffer.frameLength > 0 else { return }
        let ratio = Self.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded(.up)) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: converter.outputFormat, frameCapacity: capacity) else { return }
        let feeder = BufferFeeder(buffer)
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, inputStatus in
            feeder.next(inputStatus)
        }
        guard status != .error, out.frameLength > 0, let channel = out.int16ChannelData else { return }
        let data = Data(bytes: channel[0], count: Int(out.frameLength) * MemoryLayout<Int16>.size)
        let ready: [Data] = lock.withLock { finished ? [] : chunker.append(data) }
        for chunk in ready {
            chunkContinuation.yield(chunk)
            levelContinuation.yield(PCMLevel.rms(chunk))
        }
    }

    // MARK: - Состояние и уведомления

    private func setState(_ state: MicState) {
        let changed: Bool = lock.withLock {
            guard !finished || state == .idle, currentState != state else { return false }
            currentState = state
            return true
        }
        if changed { stateContinuation.yield(state) }
    }

    private func observe() {
        let center = NotificationCenter.default
        var list: [NSObjectProtocol] = []
        // Смена устройства или частоты: движок останавливается сам — запускаем заново.
        list.append(center.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: nil) { [weak self] _ in
            guard let self, self.state == .running else { return }
            self.restart()
        })
        #if os(iOS)
        list.append(contentsOf: observeSession())
        #endif
        lock.withLock { observers.append(contentsOf: list) }
    }

    private static func describe(_ error: Error) -> String {
        (error as? MicError)?.text ?? "Микрофон не включился: \(error.localizedDescription)"
    }

    // MARK: - Только iOS: AVAudioSession

    #if os(iOS)
    private static func activateSession() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement, options: [])
        try session.setActive(true)
    }

    private func observeSession() -> [NSObjectProtocol] {
        let center = NotificationCenter.default
        let session = AVAudioSession.sharedInstance()
        let interruption = center.addObserver(
            forName: AVAudioSession.interruptionNotification, object: session, queue: nil
        ) { [weak self] note in
            guard let self,
                  let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  let type = AVAudioSession.InterruptionType(rawValue: raw)
            else { return }
            switch type {
            case .began:
                self.setState(.interrupted)
            case .ended:
                // Возобновляем всегда, даже без shouldResume: разговор, который мы слушаем, ещё идёт.
                self.restart()
            @unknown default:
                break
            }
        }
        let route = center.addObserver(
            forName: AVAudioSession.routeChangeNotification, object: session, queue: nil
        ) { [weak self] _ in
            guard let self, self.state == .running, !self.engine.isRunning else { return }
            self.restart()
        }
        return [interruption, route]
    }
    #endif
}

/// Отдаёт конвертеру один входной буфер, потом «данных пока нет»: так AVAudioConverter
/// сохраняет состояние передискретизации между буферами tap.
private final class BufferFeeder: @unchecked Sendable {
    private var buffer: AVAudioPCMBuffer?

    init(_ buffer: AVAudioPCMBuffer) {
        self.buffer = buffer
    }

    func next(_ status: UnsafeMutablePointer<AVAudioConverterInputStatus>) -> AVAudioBuffer? {
        guard let b = buffer else {
            status.pointee = .noDataNow
            return nil
        }
        buffer = nil
        status.pointee = .haveData
        return b
    }
}

struct MicError: Error {
    let text: String
    init(_ text: String) { self.text = text }
}
