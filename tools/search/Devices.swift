import CoreML
import Foundation
import Metal
import MetalPerformanceShaders

/// A session serializes access to this object's reusable buffers. The only
/// parallel caller uses a different Core ML model and a disjoint query block.
final class MetalSearch: @unchecked Sendable {
    private let queue: MTLCommandQueue
    private let kernel: MPSMatrixMultiplication
    private let documents: MPSMatrix, queries: MPSMatrix, output: MPSMatrix
    private let rows: Int

    init(_ index: Index) throws {
        guard let device = MTLCreateSystemDefaultDevice(), MPSSupportsMTLDevice(device),
              let queue = device.makeCommandQueue() else { throw SearchError("metal_unavailable") }
        self.queue = queue
        self.rows = index.count
        func matrix(_ n: Int, _ columns: Int) throws -> MPSMatrix {
            let stride = MPSMatrixDescriptor.rowBytes(fromColumns: columns, dataType: .float32)
            guard let buffer = device.makeBuffer(length: n * stride, options: .storageModeShared)
            else { throw SearchError("metal_allocation_failed") }
            let desc = MPSMatrixDescriptor(rows: n, columns: columns, rowBytes: stride, dataType: .float32)
            return MPSMatrix(buffer: buffer, descriptor: desc)
        }
        documents = try matrix(index.count, dimension)
        queries = try matrix(blockSize, dimension)
        output = try matrix(index.count, blockSize)
        kernel = MPSMatrixMultiplication(device: device, transposeLeft: false, transposeRight: true,
                  resultRows: index.count, resultColumns: blockSize, interiorColumns: dimension, alpha: 1, beta: 0)
        let stride = documents.rowBytes / MemoryLayout<Float>.stride
        let p = documents.data.contents().assumingMemoryBound(to: Float.self)
        p.initialize(repeating: 0, count: stride * index.count)
        for row in 0..<index.count {
            nonzeroColumns(index.words, start: row * wordsPerRow) { p[row * stride + $0] = 1 }
        }
    }

    func scores(_ block: [[UInt64]]) throws -> [UInt16] {
        try require(block.count == blockSize, "invalid_metal_input")
        let stride = queries.rowBytes / MemoryLayout<Float>.stride
        let p = queries.data.contents().assumingMemoryBound(to: Float.self)
        p.initialize(repeating: 0, count: blockSize * stride)
        for (q, words) in block.enumerated() {
            nonzeroColumns(words, start: 0) { p[q * stride + $0] = 1 }
        }
        guard let command = queue.makeCommandBuffer() else { throw SearchError("metal_execution_failed") }
        kernel.encode(commandBuffer: command, leftMatrix: documents, rightMatrix: queries, resultMatrix: output)
        command.commit()
        command.waitUntilCompleted()
        try require(command.status == .completed && command.error == nil, "metal_execution_failed")
        let src = output.data.contents().assumingMemoryBound(to: Float.self)
        let outStride = output.rowBytes / MemoryLayout<Float>.stride
        var scores = [UInt16](repeating: 0, count: rows * blockSize)
        for row in 0..<rows {
            for q in 0..<blockSize {
                let v = src[row * outStride + q]
                guard let score = UInt16(exactly: v), score <= dimension else { throw SearchError("metal_invalid_score") }
                scores[row * blockSize + q] = score
            }
        }
        return scores
    }
}

final class CompiledSearch {
    private let model: MLModel
    private let input: MLMultiArray
    private let rows: Int
    private struct Receipt: Decodable {
        let version: Int
        let index_sha256: String
        let files: [String: String]
    }

    // Apple's plan loader can terminate the process on an incomplete compiled
    // bundle. Check the atomic builder output before handing it to the framework.
    private static func intact(_ url: URL, index: Index) -> Bool {
        do {
            let receiptName = "afm-search-build.json"
            let receiptURL = url.appendingPathComponent(receiptName)
            let keys: Set<URLResourceKey> = [.isDirectoryKey, .isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]
            let bundleInfo = try url.resourceValues(forKeys: keys)
            let info = try receiptURL.resourceValues(forKeys: keys)
            guard bundleInfo.isDirectory == true, bundleInfo.isSymbolicLink != true,
                  info.isRegularFile == true, info.isSymbolicLink != true else { return false }
            let receiptFile = try FileHandle(forReadingFrom: receiptURL)
            defer { try? receiptFile.close() }
            let receipt = try JSONDecoder().decode(Receipt.self, from: boundedRead(receiptFile, limit: 1 << 20))
            guard receipt.version == 1, receipt.index_sha256 == index.fingerprint,
                  (1...1024).contains(receipt.files.count),
                  let walker = FileManager.default.enumerator(at: url, includingPropertiesForKeys: Array(keys))
            else { return false }
            var checked = 0, remaining = 256 << 20
            for case let file as URL in walker {
                let info = try file.resourceValues(forKeys: keys)
                if info.isSymbolicLink == true { return false }
                if info.isDirectory == true { continue }
                let path = file.pathComponents.dropFirst(url.pathComponents.count).joined(separator: "/")
                if path == receiptName { continue }
                guard info.isRegularFile == true, let size = info.fileSize, size <= remaining,
                      let expected = receipt.files[path] else { return false }
                let input = try FileHandle(forReadingFrom: file)
                defer { try? input.close() }
                let data = try boundedRead(input, limit: remaining)
                guard digest(data) == expected else { return false }
                checked += 1
                remaining -= data.count
            }
            return checked == receipt.files.count
        } catch { return false }
    }

    private static func prefersANE(_ block: MLModelStructure.Program.Block, _ plan: MLComputePlan) -> Bool {
        for op in block.operations {
            if op.operatorName.hasSuffix(".conv"), let usage = plan.deviceUsage(for: op),
               case .neuralEngine = usage.preferred { return true }
            if op.blocks.contains(where: { prefersANE($0, plan) }) { return true }
        }
        return false
    }

    init(_ index: Index) async throws {
        let url = index.directory.appendingPathComponent("search-\(index.fingerprint).mlmodelc")
        try require(FileManager.default.fileExists(atPath: url.path), "coreml_index_missing")
        try require(Self.intact(url, index: index), "coreml_bundle_unverified")
        let config = MLModelConfiguration()
        config.computeUnits = .cpuAndNeuralEngine
        let plan = try await MLComputePlan.load(contentsOf: url, configuration: config)
        guard case .program(let program) = plan.modelStructure,
              program.functions.values.contains(where: { Self.prefersANE($0.block, plan) })
        else { throw SearchError("coreml_plan_not_ane_preferred") }
        let loaded = try await MLModel.load(contentsOf: url, configuration: config)
        let meta = loaded.modelDescription.metadata[.creatorDefinedKey] as? [String:String]
        try require(meta?["afmcli_search_index_sha256"] == index.fingerprint, "coreml_index_mismatch")
        let query = loaded.modelDescription.inputDescriptionsByName["query"]?.multiArrayConstraint
        let scores = loaded.modelDescription.outputDescriptionsByName["scores"]?.multiArrayConstraint
        try require(query?.dataType == .float16 && query?.shape.map(\.intValue) == [1,dimension,1,blockSize] &&
                    scores?.dataType == .float16 && scores?.shape.map(\.intValue) == [1,index.count,1,blockSize],
                    "coreml_shape_mismatch")
        self.model = loaded
        self.rows = index.count
        self.input = try MLMultiArray(shape: [1,NSNumber(value: dimension),1,NSNumber(value: blockSize)], dataType: .float16)
    }

    func scores(_ block: [[UInt64]]) throws -> [UInt16] {
        try require(block.count == blockSize, "invalid_coreml_input")
        input.withUnsafeMutableBytes { bytes, strides in
            let dst = bytes.bindMemory(to: Float16.self)
            dst.initialize(repeating: 0)
            for (q, words) in block.enumerated() {
                nonzeroColumns(words, start: 0) { dst[$0 * strides[1] + q * strides[3]] = 1 }
            }
        }
        let provider = try MLDictionaryFeatureProvider(dictionary: ["query": MLFeatureValue(multiArray: input)])
        let prediction = try model.prediction(from: provider)
        guard let values = prediction.featureValue(for: "scores")?.multiArrayValue,
              values.dataType == .float16, values.shape.map(\.intValue) == [1,rows,1,blockSize]
        else { throw SearchError("coreml_shape_mismatch") }
        let stride = values.strides.map(\.intValue)
        return try values.withUnsafeBytes { raw in
            let src = raw.bindMemory(to: Float16.self)
            var result = [UInt16](repeating: 0, count: rows * blockSize)
            for row in 0..<rows {
                for q in 0..<blockSize {
                    guard let n = UInt16(exactly: Float(src[row*stride[1] + q*stride[3]])), n <= dimension
                    else { throw SearchError("coreml_invalid_score") }
                    result[row*blockSize + q] = n
                }
            }
            return result
        }
    }
}
