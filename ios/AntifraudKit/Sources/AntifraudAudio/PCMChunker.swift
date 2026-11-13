import Foundation

/// Нарезка потока PCM на куски ровно по `chunkSize` байт — чистая часть MicCapture без AVFoundation.
/// По умолчанию 8000 байт = 250 мс звука 16 кГц моно Int16.
public struct PCMChunker: Sendable {
    public let chunkSize: Int
    private var buffer = Data()

    public init(chunkSize: Int = 8000) {
        precondition(chunkSize > 0 && chunkSize % 2 == 0, "кусок — целое число отсчётов Int16")
        self.chunkSize = chunkSize
    }

    /// Сколько байт ждёт, пока наберётся кусок.
    public var pendingBytes: Int { buffer.count }

    /// Добавить байты; вернуть готовые куски (может быть ни одного или несколько).
    public mutating func append(_ data: Data) -> [Data] {
        buffer.append(data)
        guard buffer.count >= chunkSize else { return [] }
        var out: [Data] = []
        var offset = buffer.startIndex
        while buffer.endIndex - offset >= chunkSize {
            out.append(Data(buffer[offset..<(offset + chunkSize)]))
            offset += chunkSize
        }
        buffer = Data(buffer[offset...])
        return out
    }

    /// Остановка: остаток — каким получился (без последнего непарного байта). Пусто — nil.
    public mutating func flush() -> Data? {
        let even = buffer.count - buffer.count % 2
        defer { buffer = Data() }
        guard even > 0 else { return nil }
        return Data(buffer.prefix(even))
    }
}

/// Громкость куска PCM s16le: 0…1 (среднеквадратичное), для индикатора записи.
public enum PCMLevel {
    public static func rms(_ data: Data) -> Float {
        let count = data.count / 2
        guard count > 0 else { return 0 }
        var sum: Double = 0
        data.withUnsafeBytes { raw in
            for i in 0..<count {
                let lo = UInt16(raw[2 * i])
                let hi = UInt16(raw[2 * i + 1])
                let v = Double(Int16(bitPattern: lo | hi << 8)) / 32768
                sum += v * v
            }
        }
        return Float((sum / Double(count)).squareRoot())
    }
}
