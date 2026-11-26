import CryptoKit
import Foundation

let dimension = 1024
let wordsPerRow = dimension / 64
let blockSize = 1024
let minimumAcceleratedRows = 4096
// The persistent FP32 document and output matrices together use at most 128 MiB.
let maximumAcceleratedRows = 16_384
let indexName = "afm-search-index.json"

struct SearchError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

func require(_ condition: Bool, _ reason: String) throws {
    if !condition { throw SearchError(reason) }
}

func digest(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

struct Document: Decodable {
    let id: String
    let text: String
}

private struct DiskIndex: Codable {
    var version: Int
    var ids: [String]
    var bits: Data
}

/// Binary occupancy of hashed words, word pairs and character n-grams.
/// The lookup cache is bounded across requests in a persistent process.
final class Features {
    private var cache = [String:Int]()
    private func column(_ key: String) -> Int {
        if let index = cache[key] { return index }
        let bytes = SHA256.hash(data: Data(key.utf8))
        let index = bytes.withUnsafeBytes { ((Int($0[2]) << 8) | Int($0[3])) % dimension }
        if cache.count < 65_536 && key.utf8.count <= 128 { cache[key] = index }
        return index
    }

    func vector(_ text: String) -> [UInt64] {
        var row = [UInt64](repeating: 0, count: wordsPerRow)
        var previous: String?
        var token = String.UnicodeScalarView()
        var kind = 0
        func add(_ key: String) {
            let c = column(key)
            row[c / 64] |= UInt64(1) << (c % 64)
        }
        func finish() {
            if token.isEmpty { return }
            let scalars = Array(token)
            let word = String(token)
            if kind == 1 {
                add("word:" + word)
                if let previous { add("words:" + previous + " " + word) }
                previous = word
            } else {
                previous = nil
            }
            let lengths = kind == 1 ? 3...3 : 1...3
            for n in lengths where scalars.count >= n {
                for i in 0...(scalars.count - n) {
                    let piece = String(String.UnicodeScalarView(scalars[i..<(i+n)]))
                    add((kind == 1 ? "lat3:" : "cjk:") + piece)
                }
            }
            token.removeAll(keepingCapacity: true)
        }
        let normalized = text.precomposedStringWithCompatibilityMapping
            .folding(options: .caseInsensitive, locale: Locale(identifier: "en_US_POSIX"))
        for scalar in normalized.unicodeScalars {
            let v = scalar.value
            let cjk = (0x3041...0x30ff).contains(v) || (0x3400...0x9fff).contains(v) || v == 0x3005
            let next = cjk ? 2 : (CharacterSet.alphanumerics.contains(scalar) ? 1 : 0)
            if next != kind { finish(); kind = next }
            if next != 0 { token.append(scalar) }
        }
        finish()
        return row
    }
}

struct Index {
    let ids: [String]
    let words: [UInt64]
    let fingerprint: String
    let directory: URL
    var count: Int { ids.count }
    var supportsDense: Bool { (minimumAcceleratedRows...maximumAcceleratedRows).contains(count) }

    static func write(_ docs: [Document], to directory: URL) throws -> String {
        let ids = docs.map(\.id)
        try validate(ids)
        try require(docs.allSatisfy { $0.text.utf8.count <= 65_536 }, "each document may contain at most 65536 UTF-8 bytes")
        let features = Features()
        var data = Data(capacity: ids.count * wordsPerRow * 8)
        for doc in docs {
            let row = features.vector(doc.text).map(\.littleEndian)
            row.withUnsafeBytes { data.append(contentsOf: $0) }
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let contents = try encoder.encode(DiskIndex(version: 1, ids: ids, bits: data))
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try contents.write(to: directory.appendingPathComponent(indexName), options: .atomic)
        return digest(contents)
    }

    private static func validate(_ ids: [String]) throws {
        try require((1...65_536).contains(ids.count), "document count must be between 1 and 65536")
        try require(ids.allSatisfy { !$0.isEmpty && $0.utf8.count <= 512 } && Set(ids).count == ids.count,
                    "document IDs must be unique, nonempty and at most 512 UTF-8 bytes")
    }

    init(_ directory: URL) throws {
        let path = directory.appendingPathComponent(indexName)
        let input = try FileHandle(forReadingFrom: path)
        defer { try? input.close() }
        let contents = try boundedRead(input, limit: 128 << 20)
        let disk = try JSONDecoder().decode(DiskIndex.self, from: contents)
        try Self.validate(disk.ids)
        try require(disk.version == 1 && disk.bits.count == disk.ids.count * wordsPerRow * 8, "incompatible index")
        var decoded = [UInt64](repeating: 0, count: disk.bits.count / 8)
        _ = decoded.withUnsafeMutableBytes { disk.bits.copyBytes(to: $0) }
        self.words = decoded.map(UInt64.init(littleEndian:))
        self.ids = disk.ids
        self.fingerprint = digest(contents)
        self.directory = directory
    }

    func cpu(_ queries: [[UInt64]]) -> [UInt16] {
        var result = [UInt16](repeating: 0, count: count * queries.count)
        let packed = queries.flatMap { $0 }
        words.withUnsafeBufferPointer { docs in
            packed.withUnsafeBufferPointer { terms in
                for row in ids.indices {
                    let offset = row * wordsPerRow
                    for q in queries.indices {
                        let start = q * wordsPerRow
                        var score = 0
                        for w in 0..<wordsPerRow {
                            score += (docs[offset + w] & terms[start + w]).nonzeroBitCount
                        }
                        result[row * queries.count + q] = UInt16(score)
                    }
                }
            }
        }
        return result
    }
}

func boundedRead(_ file: FileHandle, limit: Int) throws -> Data {
    var data = Data()
    while let next = try file.read(upToCount: 65_536), !next.isEmpty {
        try require(next.count <= limit - data.count, "input exceeds byte limit")
        data.append(next)
    }
    return data
}

func nonzeroColumns(_ words: [UInt64], start: Int, _ visit: (Int) -> Void) {
    for i in 0..<wordsPerRow {
        var word = words[start + i]
        while word != 0 {
            visit(64 * i + word.trailingZeroBitCount)
            word &= word - 1
        }
    }
}
