import CoreML
import CryptoKit
import Foundation
import Metal

struct Failure: Error, CustomStringConvertible {
    let description: String
    init(_ message: String) { description = message }
}

typealias Object = [String: Any]
func now() -> UInt64 { DispatchTime.now().uptimeNanoseconds }
func require(_ condition: Bool, _ message: String) throws {
    if !condition { throw Failure(message) }
}
func emit(_ object: Object) throws {
    var data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    data.append(10)
    try FileHandle.standardOutput.write(contentsOf: data)
}
func json(_ data: Data) throws -> Object {
    guard let o = try JSONSerialization.jsonObject(with: data) as? Object else { throw Failure("Expected a JSON object") }
    return o
}

func device(_ d: MLComputeDevice) -> Object {
    switch d {
    case .cpu: return ["kind": "cpu", "name": "CPU"]
    case .gpu(let g): return ["kind": "gpu", "name": g.metalDevice.name]
    case .neuralEngine(let n): return ["kind": "neuralEngine", "name": "Neural Engine", "core_count": n.totalCoreCount]
    @unknown default: return ["kind": "unknown", "name": d.description]
    }
}
func configuration(_ policy: String) throws -> MLModelConfiguration {
    let configuration = MLModelConfiguration()
    switch policy {
    case "cpuOnly": configuration.computeUnits = .cpuOnly
    case "cpuAndNeuralEngine": configuration.computeUnits = .cpuAndNeuralEngine
    default: throw Failure("Unknown compute policy")
    }
    return configuration
}

func operations(_ block: MLModelStructure.Program.Block, plan: MLComputePlan) -> [Object] {
    var output: [Object] = []
    for op in block.operations {
        let usage = plan.deviceUsage(for: op)
        let entry: Object = ["operator": op.operatorName, "outputs": op.outputs.map(\.name),
                             "preferred": usage.map { device($0.preferred) } as Any? ?? NSNull(),
                             "supported": usage.map { $0.supported.map(device) } as Any? ?? NSNull(),
                             "estimated_cost_weight": plan.estimatedCost(of: op)?.weight as Any? ?? NSNull()]
        output.append(entry)
        for inner in op.blocks { output += operations(inner, plan: plan) }
    }
    return output
}

struct Shape: Decodable {
    let dimension: Int
    let row_count: Int
    let queries: Int
}

func pack(_ arrays: [[Int]], shape: Shape) throws -> MLMultiArray {
    try require(arrays.count == shape.queries, "Wrong query count")
    let input = try MLMultiArray(shape: [1, NSNumber(value: shape.dimension), 1, NSNumber(value: shape.queries)], dataType: .float16)
    try input.withUnsafeMutableBytes { bytes, strides in
        let buffer = bytes.bindMemory(to: Float16.self)
        buffer.initialize(repeating: 0)
        for (q, cols) in arrays.enumerated() {
            for j in cols {
                try require(j >= 0 && j < shape.dimension, "Feature column out of range")
                buffer[j * strides[1] + q * strides[3]] = 1
            }
        }
    }
    return input
}

// Query-major Q×K float32 on disk, NC1Q fp16 in Core ML. Prefix reads permit
// a single canonical query file to cover a whole shape sweep.
func packFile(_ url: URL, shape: Shape, offset: Int) throws -> MLMultiArray {
    let source = try Data(contentsOf: url, options: .mappedIfSafe)
    try require(offset >= 0 && offset <= source.count / (shape.dimension * 4) - shape.queries, "Query file too small")
    let input = try MLMultiArray(shape: [1, NSNumber(value: shape.dimension), 1, NSNumber(value: shape.queries)], dataType: .float16)
    try source.withUnsafeBytes { raw in
        let words = raw.bindMemory(to: Float32.self)
        try input.withUnsafeMutableBytes { bytes, strides in
            let dest = bytes.bindMemory(to: Float16.self)
            for j in 0..<shape.dimension {
                for q in 0..<shape.queries {
                    let value = words[(offset + q) * shape.dimension + j]
                    try require(value == 0 || value == 1, "Expected binary query features")
                    dest[j * strides[1] + q * strides[3]] = Float16(value)
                }
            }
        }
    }
    return input
}

func runPrediction(_ provider: MLDictionaryFeatureProvider, model: MLModel, shape: Shape,
                   request: Object, started: UInt64, packed: UInt64) throws -> Object {
    let t = now()
    let output = try model.prediction(from: provider)
    let predicted = now()
    guard let array = output.featureValue(for: "scores")?.multiArrayValue else { throw Failure("Missing scores output") }
    try require(array.dataType == .float16 && array.shape.map(\.intValue) == [1, shape.row_count, 1, shape.queries], "Unexpected result tensor")
    // The public API does not guarantee contiguous NCHW output. Decode by the
    // actual strides before calculating the full integer score or rank digest.
    let strides = array.strides.map(\.intValue), cells = shape.row_count * shape.queries
    var host = Data(count: cells * MemoryLayout<Float16>.stride)
    try host.withUnsafeMutableBytes { dest in
        try array.withUnsafeBytes { source in
            let dst = dest.bindMemory(to: UInt16.self), src = source.bindMemory(to: UInt16.self)
            let qCount = shape.queries
            let last = (shape.row_count-1)*strides[1] + (qCount-1)*strides[3]
            try require(last < src.count && strides[1] >= 0 && strides[3] >= 0, "Result strides exceed readable buffer")
            if strides[1] == qCount && (qCount == 1 || strides[3] == 1) {
                dest.copyMemory(from: UnsafeRawBufferPointer(start: source.baseAddress!, count: dest.count))
            } else if strides[3] == 1 {
                for r in 0..<shape.row_count {
                    memcpy(dst.baseAddress! + r*qCount, src.baseAddress! + r*strides[1], qCount*2)
                }
            } else {
                for r in 0..<shape.row_count {
                    for q in 0..<qCount { dst[r*qCount + q] = src[r*strides[1] + q*strides[3]] }
                }
            }
        }
    }
    let read = now()
    var response: Object = ["id": request["id"] ?? NSNull(), "start_ns": started,
                            "predict_start_ns": t, "prediction_returned_ns": predicted,
                            "complete_ns": read, "pack_ns": packed,
                            "predict_ns": predicted-t, "readback_ns": read-predicted]
    let compact = shape.queries != 1 || request["compact"] as? Bool == true
    if compact {
        response["scores_sha256_f16le"] = SHA256.hash(data: host).map { String(format: "%02x", $0) }.joined()
        response["elements"] = cells
    }
    // A validated FP16 byte digest is sufficient on repeated batched calls.
    // Materialization timestamps above always include a complete host copy.
    if !compact || request["fp16_digest_only"] as? Bool != true {
        let values: [UInt16] = try host.withUnsafeBytes { raw in
            try raw.bindMemory(to: Float16.self).map { half in
                let value = Float(half)
                guard value.isFinite, value >= 0, value <= Float(shape.dimension), value.rounded() == value else { throw Failure("Noninteger or out-of-range result") }
                return UInt16(value)
            }
        }
        if !compact {
            response["scores"] = values
            response["rank"] = values.indices.sorted { values[$0] == values[$1] ? $0 < $1 : values[$0] > values[$1] }
        } else {
            let data = values.withUnsafeBytes { Data($0) }
            if let path = request["save_scores"] as? String { try data.write(to: URL(fileURLWithPath: path), options: .atomic) }
            response["scores_sha256_u16le"] = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        }
    }
    response["postprocess_ns"] = now()-read
    response["native_total_ns"] = now()-started
    return response
}

func serve(model: MLModel, shape: Shape) throws {
    var prepared: [String: MLDictionaryFeatureProvider] = [:]
    while let line = readLine() {
        do {
            let request = try json(Data(line.utf8))
            let t = now()
            if let path = request["load_query_file"] as? String, let key = request["key"] as? String {
                let array = try packFile(URL(fileURLWithPath: path), shape: shape, offset: request["query_offset"] as? Int ?? 0)
                prepared[key] = try MLDictionaryFeatureProvider(dictionary: ["query": MLFeatureValue(multiArray: array)])
                try emit(["id": request["id"] ?? NSNull(), "prepared": key, "pack_ns": now()-t])
                continue
            }
            let input: MLDictionaryFeatureProvider
            if let key = request["prepared"] as? String {
                guard let value = prepared[key] else { throw Failure("Unknown prepared query key") }
                input = value
            } else {
                let query: [[Int]]
                if let batch = request["batch"] as? [[Int]] { query = batch }
                else if let active = request["active"] as? [Int] { query = [active] }
                else { throw Failure("Supply active, batch or prepared") }
                input = try MLDictionaryFeatureProvider(dictionary: ["query": MLFeatureValue(multiArray: pack(query, shape: shape))])
            }
            try emit(runPrediction(input, model: model, shape: shape, request: request, started: t, packed: now()-t))
        } catch { try emit(["error": String(describing: error)]) }
    }
}

@main struct Placement {
    static func main() async {
        do {
            let a = CommandLine.arguments
            try require(a.count >= 3, "Usage: placement-index compile DIRECTORY | plan/serve DIRECTORY POLICY")
            let dir = URL(fileURLWithPath: a[2], isDirectory: true)
            let modelURL = dir.appendingPathComponent("index.mlmodelc", isDirectory: true)
            if a[1] == "compile" {
                let started = now()
                let built = try await MLModel.compileModel(at: dir.appendingPathComponent("index.mlpackage"))
                let compiled = now()
                try FileManager.default.moveItem(at: built, to: modelURL)
                try emit(["compile_ns": compiled-started, "move_ns": now()-compiled])
                return
            }
            try require(a.count == 4, "Missing policy")
            let policy = a[3], config = try configuration(policy)
            if a[1] == "plan" {
                let started = now()
                let plan = try await MLComputePlan.load(contentsOf: modelURL, configuration: config)
                guard case .program(let program) = plan.modelStructure else { throw Failure("Expected ML Program") }
                try emit(["policy": policy, "load_plan_ns": now()-started,
                          "all_compute_devices": MLComputeDevice.allComputeDevices.map(device),
                          "available_compute_devices": MLModel.availableComputeDevices.map(device),
                          "functions": program.functions.mapValues { operations($0.block, plan: plan) }])
                return
            }
            try require(a[1] == "serve", "Unknown command")
            let shape = try JSONDecoder().decode(Shape.self, from: Data(contentsOf: dir.appendingPathComponent("manifest.json")))
            let started = now()
            let model = try await MLModel.load(contentsOf: modelURL, configuration: config)
            try emit(["ready": true, "policy": policy, "load_ns": now()-started,
                      "row_count": shape.row_count, "dimension": shape.dimension, "queries": shape.queries])
            try serve(model: model, shape: shape)
        } catch {
            try? FileHandle.standardError.write(contentsOf: Data((String(describing: error) + "\n").utf8))
            exit(1)
        }
    }
}
