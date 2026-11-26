import CoreML
import Foundation

struct Request: Decodable {
    let policy: String
    let dimension: Int
    let rows: [[Int]]
    let queries: [[Int]]
}

struct Trial: Encodable {
    let scores: [Int]
    let order: [Int]
    let queryTensorNS: UInt64
    let submitNS: UInt64
    let materializeNS: UInt64
    let rankingNS: UInt64
}

struct Observation: Encodable {
    let policy: String
    let decodeNS: UInt64
    let denseMatrixNS: UInt64
    let matrixTensorNS: UInt64
    let trials: [Trial]
}

enum ProbeError: Error {
    case invalidInput, invalidResult
}

func now() -> UInt64 { DispatchTime.now().uptimeNanoseconds }

@main
enum Rank {
    static func main() async throws {
        let clockStart = now()
        let raw = FileHandle.standardInput.readDataToEndOfFile()
        let request = try JSONDecoder().decode(Request.self, from: raw)
        let decoded = now()
        guard request.dimension == 1024, (1...10_000).contains(request.rows.count),
              (1...4).contains(request.queries.count),
              request.queries.allSatisfy({ $0.count == request.dimension && $0.allSatisfy { (0...2).contains($0) } && $0.reduce(0, +) <= 2048 })
        else { throw ProbeError.invalidInput }
        let policy: MLComputePolicy
        switch request.policy {
        case "cpuOnly": policy = .cpuOnly
        case "cpuAndNeuralEngine": policy = MLComputePolicy(.cpuAndNeuralEngine)
        default: throw ProbeError.invalidInput
        }
        var dense = [Float16](repeating: 0, count: request.rows.count * request.dimension)
        for (i, bins) in request.rows.enumerated() {
            for index in bins {
                guard (0..<request.dimension).contains(index) else { throw ProbeError.invalidInput }
                dense[i * request.dimension + index] = 1
            }
        }
        let prepared = now()
        let measured = try await withMLTensorComputePolicy(policy) {
            let start = now()
            let matrix = MLTensor(shape: [request.rows.count, request.dimension],
                                  scalars: dense, scalarType: Float16.self)
            let builtMatrix = now()
            var trials = [Trial]()
            for query in request.queries {
                let t0 = now()
                let vector = MLTensor(shape: [request.dimension, 1], scalars: query.map { Float16($0) },
                                      scalarType: Float16.self)
                let t1 = now()
                let product = matrix.matmul(vector)
                let t2 = now()
                let values = await product.shapedArray(of: Float16.self)
                let scalars = values.scalars
                let t3 = now()
                guard scalars.count == request.rows.count else { throw ProbeError.invalidResult }
                let scores = try scalars.map {
                    guard let n = Int(exactly: $0), (0...2048).contains(n) else { throw ProbeError.invalidResult }
                    return n
                }
                let order = scores.indices.sorted { a, b in scores[a] == scores[b] ? a < b : scores[a] > scores[b] }
                trials.append(Trial(scores: scores, order: order, queryTensorNS: t1-t0, submitNS: t2-t1,
                                    materializeNS: t3-t2, rankingNS: now()-t3))
            }
            return Observation(policy: request.policy, decodeNS: decoded-clockStart, denseMatrixNS: prepared-decoded,
                               matrixTensorNS: builtMatrix-start, trials: trials)
        }
        let output = try JSONEncoder().encode(measured)
        FileHandle.standardOutput.write(output)
    }
}
