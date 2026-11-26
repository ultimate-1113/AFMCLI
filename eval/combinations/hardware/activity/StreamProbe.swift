import Darwin
import Foundation
import FoundationModels

private func emit(_ fields: [String: Any]) throws {
    let data = try JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])
    try FileHandle.standardOutput.write(contentsOf: data + Data([10]))
}

@main
@available(macOS 27.0, *)
struct StreamProbe {
    static func main() async throws {
        let model = SystemLanguageModel.default
        guard model.isAvailable, model.variant == .coreAdvanced3 else {
            throw ProbeError.unavailable
        }
        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let prompt = String(data: input, encoding: .utf8), !prompt.isEmpty else {
            throw ProbeError.badInput
        }
        let inputTokens = try await model.tokenCount(for: prompt)
        let session = LanguageModelSession(model: model)
        let options = GenerationOptions(samplingMode: .greedy, maximumResponseTokens: 400)
        let start = DispatchTime.now().uptimeNanoseconds
        var first: UInt64?
        var result = ""
        for try await part in session.streamResponse(to: prompt, options: options) {
            if !part.content.isEmpty && first == nil {
                first = DispatchTime.now().uptimeNanoseconds
            }
            result = part.content
        }
        let end = DispatchTime.now().uptimeNanoseconds
        guard let first, !result.isEmpty else { throw ProbeError.emptyOutput }
        let outputTokens = try await model.tokenCount(for: result)
        try emit(["variant": model.variant.displayName, "input_tokens": inputTokens,
                  "output_tokens": outputTokens, "content": result,
                  "start": start, "first": first, "end": end])
    }

    enum ProbeError: Error {
        case unavailable, badInput, emptyOutput
    }
}
