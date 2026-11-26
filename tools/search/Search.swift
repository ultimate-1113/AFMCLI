import Darwin
import Foundation

enum Mode: String { case cpu, interactive, throughput }

struct Request: Decodable {
    let queries: [String]
    let top_k: Int?
    let all_scores: Bool?
}
struct Hit: Encodable {
    let id: String
    let score: UInt16
}
struct QueryResult: Encodable {
    let query_index: Int
    let hits: [Hit]
    let scores: [UInt16]?
}
struct Calibration: Encodable {
    let score_ms: [String: [Double]]
    let choice: String
}
struct Response: Encodable {
    let status = "ok"
    let mode: String
    let index_sha256: String
    var routes = ["cpu": 0, "metal": 0, "coreml_ane_preferred": 0]
    var parallel_pairs = 0
    var warmup: [String] = []
    var fallbacks: [String] = []
    var calibration: [String: Calibration] = [:]
    var results: [QueryResult] = []
}

/// All engines return document-major integer scores. Ranking and serialization
/// are shared so route changes also preserve ties and query order.
func appendResults(_ scores: [UInt16], width: Int, route: String, index: Index,
                   top: Int, all: Bool, to response: inout Response) {
    for q in 0..<width {
        var hits = [Hit]()
        var full = all ? [UInt16](repeating: 0, count: index.count) : nil
        for row in index.ids.indices {
            let score = scores[row * width + q]
            if all { full![row] = score }
            if score == 0 || top == 0 || (hits.count == top && score <= hits.last!.score) { continue }
            var lo = 0, hi = hits.count
            while lo < hi {
                let mid = (lo + hi) / 2
                if hits[mid].score >= score { lo = mid + 1 } else { hi = mid }
            }
            if hits.count == top { hits.removeLast() }
            hits.insert(Hit(id: index.ids[row], score: score), at: lo)
        }
        response.results.append(QueryResult(query_index: response.results.count, hits: hits, scores: full))
    }
    response.routes[route, default: 0] += width
}

final class Session {
    let index: Index
    let mode: Mode
    let persistent: Bool
    private let features = Features()
    private var attempted = false
    private var gpu: MetalSearch?
    private var compiled: CompiledSearch?
    private var failures = [String]()
    private var measurements: [String: Calibration] = [:]

    init(index: Index, mode: Mode, persistent: Bool) {
        self.index = index
        self.mode = mode
        self.persistent = persistent
    }

    private func failure(_ error: Error, _ fallback: String) {
        let reason = (error as? SearchError)?.description ?? fallback
        if !failures.contains(reason) { failures.append(reason) }
    }

    private func warm(_ block: [[UInt64]], reference: [UInt16]) async -> [String] {
        attempted = true
        var verified = [String]()
        do {
            let candidate = try MetalSearch(index)
            try require(try candidate.scores(block) == reference, "metal_warmup_mismatch")
            gpu = candidate
            verified.append("metal")
            var times = ["cpu": [Double](), "metal": [Double]()]
            for round in 0..<3 {
                for route in (round % 2 == 0 ? ["cpu", "metal"] : ["metal", "cpu"]) {
                    let begin = DispatchTime.now().uptimeNanoseconds
                    let scores = route == "cpu" ? index.cpu(block) : try candidate.scores(block)
                    times[route]!.append(elapsedMS(begin))
                    try require(scores == reference, "metal_warmup_mismatch")
                }
            }
            // Interactive prefers GPU within 10% of CPU to keep CPU capacity
            // available. Throughput selects the faster median for single blocks.
            let slack = mode == .interactive ? 1.10 : 1.0
            let choice = median(times["metal"]!) <= slack * median(times["cpu"]!) ? "metal" : "cpu"
            measurements["single"] = Calibration(score_ms: times, choice: choice)
        } catch { gpu = nil; verified = []; failure(error, "metal_initialization_failed") }
        // The throughput policy only uses Core ML in a pair with an available GPU.
        if mode == .throughput && gpu != nil {
            do {
                let candidate = try await CompiledSearch(index)
                try require(try candidate.scores(block) == reference, "coreml_warmup_mismatch")
                compiled = candidate
                verified.append("coreml_ane_preferred")
            } catch { failure(error, "coreml_initialization_failed") }
        }
        return verified
    }

    private func metalOrCPU(_ block: [[UInt64]]) -> ([UInt16], String) {
        if let gpu, measurements["single"]?.choice == "metal" {
            do { return (try gpu.scores(block), "metal") }
            catch { self.gpu = nil; failure(error, "metal_execution_failed") }
        }
        return (index.cpu(block), "cpu")
    }

    /// All three candidates score the same two *new*, disjoint query blocks.
    /// Each round rotates which candidate runs first. Only the score kernel,
    /// input packing and materialization vary with the route.
    private func calibratePair(_ left: [[UInt64]], _ right: [[UInt64]],
                               gpu: MetalSearch, model: CompiledSearch) async {
        let reference = (index.cpu(left), index.cpu(right))
        let routes = ["cpu", "metal", "split"]
        var times = ["cpu": [Double](), "metal": [Double](), "split": [Double]()]
        do {
            for round in 0..<3 {
                for shift in 0..<routes.count {
                    let route = routes[(shift + round) % routes.count]
                    let begin = DispatchTime.now().uptimeNanoseconds
                    let result: ([UInt16], [UInt16])
                    switch route {
                    case "cpu": result = (index.cpu(left), index.cpu(right))
                    case "metal": result = (try gpu.scores(left), try gpu.scores(right))
                    default:
                        let task = Task.detached { try gpu.scores(left) }
                        let other = Result { try model.scores(right) }
                        result = (try await task.value, try other.get())
                    }
                    times[route]!.append(elapsedMS(begin))
                    try require(result.0 == reference.0 && result.1 == reference.1, route + "_warmup_mismatch")
                }
            }
            let single = median(times["metal"]!) <= median(times["cpu"]!) ? "metal" : "cpu"
            let split = median(times["split"]!) <= 0.95 * median(times[single]!)
            measurements["pair"] = Calibration(score_ms: times, choice: split ? "split" : single)
        } catch {
            let reason = (error as? SearchError)?.description ?? ""
            if reason.hasPrefix("metal") { self.gpu = nil }
            self.compiled = nil
            failure(error, "coreml_calibration_failed")
        }
    }

    func search(_ request: Request) async throws -> Response {
        let count = request.queries.count
        let top = request.top_k ?? 10
        let all = request.all_scores ?? false
        try require(count <= 65_536 && request.queries.allSatisfy { $0.utf8.count <= 65_536 },
                    "at most 65536 queries and 65536 UTF-8 bytes per query")
        try require((0...1000).contains(top) && count * min(top, index.count) <= 1_000_000,
                    "top_k must be 0...1000 and the request may return at most 1000000 hits")
        try require(!all || count * index.count <= 16_777_216, "all_scores exceeds 16777216 cells")
        var response = Response(mode: mode.rawValue, index_sha256: index.fingerprint)
        var start = 0
        let dense = persistent && mode != .cpu && index.supportsDense && count >= blockSize
        func vectors(_ offset: Int, _ width: Int) -> [[UInt64]] {
            request.queries[offset..<(offset + width)].map(features.vector)
        }
        func append(_ scores: [UInt16], _ width: Int, _ route: String) {
            appendResults(scores, width: width, route: route, index: index, top: top, all: all, to: &response)
            start += width
        }
        if dense && !attempted {
            let first = vectors(start, blockSize)
            let reference = index.cpu(first)
            response.warmup = await warm(first, reference: reference)
            append(reference, blockSize, "cpu")
        }
        if dense && count - start >= 2 * blockSize && measurements["pair"] == nil,
           let gpu, let compiled {
            await calibratePair(vectors(start, blockSize), vectors(start + blockSize, blockSize),
                                gpu: gpu, model: compiled)
        }
        while dense && count - start >= 2 * blockSize, let gpu, let compiled {
            let left = vectors(start, blockSize)
            let right = vectors(start + blockSize, blockSize)
            if measurements["pair"]?.choice != "split" {
                if measurements["pair"]?.choice == "cpu" {
                    append(index.cpu(left), blockSize, "cpu")
                    append(index.cpu(right), blockSize, "cpu")
                } else {
                    for block in [left, right] {
                        if self.gpu != nil {
                            do { append(try gpu.scores(block), blockSize, "metal"); continue }
                            catch { self.gpu = nil; failure(error, "metal_execution_failed") }
                        }
                        append(index.cpu(block), blockSize, "cpu")
                    }
                }
                continue
            }
            let pending = Task.detached { try gpu.scores(left) }
            let rightResult = Result { try compiled.scores(right) }
            let leftResult = await pending.result
            switch leftResult {
            case .success(let scores): append(scores, blockSize, "metal")
            case .failure(let error):
                self.gpu = nil
                failure(error, "metal_execution_failed")
                append(index.cpu(left), blockSize, "cpu")
            }
            switch rightResult {
            case .success(let scores):
                append(scores, blockSize, "coreml_ane_preferred")
                if self.gpu != nil { response.parallel_pairs += 1 }
            case .failure(let error):
                self.compiled = nil
                failure(error, "coreml_execution_failed")
                let (scores, route) = metalOrCPU(right)
                append(scores, blockSize, route)
            }
        }
        while dense && gpu != nil && measurements["single"]?.choice == "metal" && count - start >= blockSize {
            let (scores, route) = metalOrCPU(vectors(start, blockSize))
            append(scores, blockSize, route)
        }
        while index.supportsDense && count - start >= blockSize {
            append(index.cpu(vectors(start, blockSize)), blockSize, "cpu")
        }
        while start < count {
            let width = min(64, count - start)
            append(index.cpu(vectors(start, width)), width, "cpu")
        }
        if dense { response.fallbacks = failures; response.calibration = measurements }
        return response
    }
}

private func elapsedMS(_ start: UInt64) -> Double { Double(DispatchTime.now().uptimeNanoseconds - start) / 1e6 }
private func median(_ values: [Double]) -> Double { values.sorted()[values.count / 2] }

private let help = """
Usage: afm-search index INDEX < documents.json
       afm-search query INDEX [--mode cpu|interactive|throughput] < request.json
       afm-search serve INDEX [--mode cpu|interactive|throughput] < requests.jsonl
Documents: [{"id":"A","text":"first document"}]
Request: {"queries":["first"],"top_k":10,"all_scores":false}
"""
private let requestLimit = 64 << 20

private func writeJSON<T: Encodable>(_ payload: T) throws {
    var bytes = try JSONEncoder().encode(payload)
    bytes.append(10)
    try FileHandle.standardOutput.write(contentsOf: bytes)
}

private func writeError(_ error: Error) throws {
    let message = (error as? SearchError)?.description ?? error.localizedDescription
    try writeJSON(["status": "error", "error": message])
}

/// Read JSONL with a bounded line buffer, preserving the next request after a
/// malformed or oversized one. readData(ofLength:) can wait for EOF on a pipe.
private func serve(_ session: Session) async throws {
    var line = Data()
    var oversized = false
    func consume(_ part: Data.SubSequence) {
        guard !oversized else { return }
        if part.count <= requestLimit - line.count { line.append(contentsOf: part) }
        else { oversized = true; line.removeAll() }
    }
    func finish() async throws {
        do {
            try require(!oversized, "input exceeds byte limit")
            let request = try JSONDecoder().decode(Request.self, from: line)
            let response = try await session.search(request)
            try writeJSON(response)
        } catch { try writeError(error) }
        line.removeAll(keepingCapacity: false)
        oversized = false
    }
    while true {
        let data = FileHandle.standardInput.availableData
        if data.isEmpty { break }
        var from = data.startIndex
        while let end = data[from...].firstIndex(of: 10) {
            consume(data[from..<end])
            try await finish()
            from = end + 1
        }
        consume(data[from...])
    }
    if !line.isEmpty || oversized { try await finish() }
}

@main
struct SearchCLI {
    static func main() async {
        do {
            let args = Array(CommandLine.arguments.dropFirst())
            if args == ["--help"] || args == ["-h"] { print(help); return }
            try require(args.count == 2 || args.count == 4, help)
            let directory = URL(fileURLWithPath: args[1], isDirectory: true)
            if args[0] == "index" {
                try require(args.count == 2, help)
                let data = try boundedRead(.standardInput, limit: 128 << 20)
                let docs = try JSONDecoder().decode([Document].self, from: data)
                let hash = try Index.write(docs, to: directory)
                try writeJSON(["status": "ok", "index_sha256": hash])
                return
            }
            try require(args[0] == "query" || args[0] == "serve", help)
            let name = args.count == 4 ? args[3] : "interactive"
            guard let mode = Mode(rawValue: name), args.count == 2 || args[2] == "--mode"
            else { throw SearchError(help) }
            let session = Session(index: try Index(directory), mode: mode, persistent: args[0] == "serve")
            if session.persistent { try await serve(session); return }
            let data = try boundedRead(.standardInput, limit: requestLimit)
            let request = try JSONDecoder().decode(Request.self, from: data)
            try writeJSON(try await session.search(request))
        } catch {
            try? writeError(error)
            exit(1)
        }
    }
}
