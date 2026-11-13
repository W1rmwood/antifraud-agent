import Foundation
import Testing
@testable import AntifraudAudio

@Suite("Нарезка PCM на куски")
struct PCMChunkerTests {
    func bytes(_ n: Int, start: Int = 0) -> Data { Data((0..<n).map { UInt8(truncatingIfNeeded: $0 + start) }) }

    @Test("Куски ровно по 8000 байт, порядок байт сохраняется")
    func exactChunks() {
        var c = PCMChunker()
        var out: [Data] = []
        // Буферы tap неровные: 48 кГц → 16 кГц даёт по 1365–1366 отсчётов.
        var total = Data()
        for i in 0..<30 {
            let piece = bytes(2730 + (i % 2) * 2, start: total.count)
            total.append(piece)
            out += c.append(piece)
        }
        #expect(out.allSatisfy { $0.count == 8000 })
        #expect(out.count == total.count / 8000)
        #expect(out.reduce(Data(), +) == total.prefix(out.count * 8000))
        #expect(c.pendingBytes == total.count % 8000)
    }

    @Test("Один большой буфер — несколько кусков сразу")
    func bigBuffer() {
        var c = PCMChunker(chunkSize: 8000)
        let out = c.append(bytes(8000 * 3 + 100))
        #expect(out.count == 3)
        #expect(c.pendingBytes == 100)
    }

    @Test("Остановка: остаток — какой получился, непарный байт отбрасывается")
    func flush() {
        var c = PCMChunker(chunkSize: 8)
        #expect(c.append(bytes(13)).count == 1)
        #expect(c.flush() == bytes(13).subdata(in: 8..<12))
        #expect(c.flush() == nil)
        #expect(c.pendingBytes == 0)
    }

    @Test("Пустой поток — ни одного куска")
    func empty() {
        var c = PCMChunker()
        #expect(c.append(Data()).isEmpty)
        #expect(c.flush() == nil)
    }

    @Test("Громкость: тишина 0, полная шкала около 1")
    func level() {
        #expect(PCMLevel.rms(Data(count: 800)) == 0)
        var loud = Data()
        for i in 0..<400 {
            let v: Int16 = i % 2 == 0 ? .max : .min
            withUnsafeBytes(of: v.littleEndian) { loud.append(contentsOf: $0) }
        }
        #expect(PCMLevel.rms(loud) > 0.99)
        #expect(PCMLevel.rms(Data()) == 0)
    }
}
