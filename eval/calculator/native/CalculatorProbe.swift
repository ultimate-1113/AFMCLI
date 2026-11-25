import Darwin
import Foundation
import FoundationModels

// One JSON object per event. Write immediately so an externally timed-out job retains callbacks.
func emit(_ fields: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])
    FileHandle.standardOutput.write(data + Data([10]))
}

func elapsed(_ start: UInt64) -> Double {
    Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000
}

struct ProbeError: LocalizedError {
    var message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

enum Angle: String { case deg, rad }

struct Calculation: Decodable {
    let expression: String
    let angle_unit: String
    let value: String
    let approximate: Bool
}

struct CalculatorRecord: Decodable {
    let status: String
    let result: Calculation?
    let notes: [String]?
}

actor CalculatorHost {
    let executable: URL
    let angle: Angle
    let start: UInt64
    let source: String
    var invocations = 0

    init(path: String, angle: Angle, start: UInt64, source: String) throws {
        guard path.hasPrefix("/"), FileManager.default.isExecutableFile(atPath: path) else {
            throw ProbeError("calculator must be an executable at an explicit absolute path")
        }
        self.executable = URL(fileURLWithPath: path)
        self.angle = angle
        self.start = start
        self.source = source
    }

    func call(_ arguments: GeneratedContent) -> String {
        let begin = DispatchTime.now().uptimeNanoseconds
        invocations += 1
        var event: [String: Any] = [
            "event": "callback", "source": source, "sequence": invocations,
            "tool": ScientificCalculator.toolName, "angle": angle.rawValue,
            "started_ms": elapsed(start), "started_process": false, "value": NSNull(),
        ]
        let answer: String
        do {
            let expression = try arguments.value(String.self, forProperty: "expression")
            event["expression"] = expression
            guard !expression.isEmpty, expression.utf8.count <= 4096,
                  !expression.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else {
                throw ProbeError("supply one arithmetic expression of at most 4096 UTF-8 bytes")
            }
            guard invocations <= 8 else { throw ProbeError("calculator call limit reached") }

            // Only the developer chooses this program. The model's string is solely stdin data.
            let process = Process()
            let input = Pipe(), output = Pipe()
            process.executableURL = executable
            process.arguments = ["calc", "--angle", angle.rawValue]
            process.standardInput = input
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            try process.run()
            event["started_process"] = true
            defer {
                if process.isRunning { process.terminate() }
                process.waitUntilExit()
            }
            do {
                try input.fileHandleForWriting.write(contentsOf: Data(expression.utf8))
                try input.fileHandleForWriting.close()
            } catch {
                try? input.fileHandleForWriting.close()
                throw error
            }
            // The fixed calculator emits a single bounded record. Drain stdout before waiting.
            let response = try output.fileHandleForReading.readToEnd() ?? Data()
            process.waitUntilExit()
            event["backend_exit"] = process.terminationStatus
            let record = try JSONDecoder().decode(CalculatorRecord.self, from: response)
            event["backend_status"] = record.status
            guard process.terminationStatus == 0, record.status == "ok", let result = record.result else {
                throw ProbeError(record.notes?.first ?? "calculator rejected the expression")
            }
            guard result.angle_unit == angle.rawValue, result.expression == expression.trimmingCharacters(in: .whitespaces), !result.value.isEmpty else {
                throw ProbeError("calculator returned an inconsistent result")
            }
            event["value"] = result.value
            event["approximate"] = result.approximate
            answer = result.value
        } catch {
            event["error"] = error.localizedDescription
            answer = "Calculator error: " + error.localizedDescription
        }
        event["returned"] = answer
        event["elapsed_ms"] = elapsed(begin)
        event["finished_ms"] = elapsed(start)
        emit(event)
        return answer
    }
}

struct ScientificCalculator: Tool {
    static let toolName = "scientificCalculator"
    let name = ScientificCalculator.toolName
    let description: String
    let parameters: GenerationSchema
    let host: CalculatorHost

    init(host: CalculatorHost, angle: Angle) throws {
        self.host = host
        let unit = angle == .deg ? "degrees" : "radians"
        self.description = "Evaluate an arithmetic or scientific expression and return its numerical value. Angles use " + unit + "."
        let expression = DynamicGenerationSchema.Property(
            name: "expression", description: "Full formula as written. Use +, -, *, /, ^, parentheses and named functions such as sin, sqrt or log10.",
            schema: DynamicGenerationSchema(type: String.self)
        )
        let root = DynamicGenerationSchema(name: "CalculatorArguments", properties: [expression])
        self.parameters = try GenerationSchema(root: root, dependencies: [])
    }

    @concurrent func call(arguments: GeneratedContent) async throws -> String {
        await host.call(arguments)
    }
}

func transcriptEvents(_ session: LanguageModelSession) -> [[String: Any]] {
    var events: [[String: Any]] = []
    for entry in session.transcript {
        switch entry {
        case .toolCalls(let calls):
            for call in calls {
                let args = (try? JSONSerialization.jsonObject(with: Data(call.arguments.jsonString.utf8))) ?? NSNull()
                events.append(["kind": "tool_call", "id": call.id, "tool": call.toolName, "arguments": args])
            }
        case .toolOutput(let output):
            let segments: [String] = output.segments.compactMap { segment in
                switch segment {
                case .text(let text): return text.content
                case .structure(let structure): return structure.content.jsonString
                default: return nil
                }
            }
            events.append(["kind": "tool_output", "id": output.id, "tool": output.toolName, "segments": segments])
        default: break
        }
    }
    return events
}

func modelInfo(_ model: SystemLanguageModel) -> [String: Any] {
    ["event": "model", "route": "system", "available": model.isAvailable,
     "availability": String(describing: model.availability), "variant": model.variant.displayName,
     "core_advanced_3": model.variant == .coreAdvanced3,
     "os": ProcessInfo.processInfo.operatingSystemVersionString]
}

@main struct CalculatorProbe {
    static func main() async {
        do {
            try await run()
        } catch {
            emit(["event": "error", "error": error.localizedDescription])
            exit(1)
        }
    }

    static func run() async throws {
        let args = Array(CommandLine.arguments.dropFirst())
        if args == ["doctor"] {
            let model = SystemLanguageModel.default
            emit(modelInfo(model))
            guard model.isAvailable, model.variant == .coreAdvanced3 else { exit(2) }
            return
        }
        guard args.count == 3, ["registered", "unregistered", "check-calculator"].contains(args[0]),
              let angle = Angle(rawValue: args[2]) else {
            throw ProbeError("use doctor, or registered|unregistered|check-calculator /absolute/afmcli deg|rad")
        }
        let bytes = try FileHandle.standardInput.readToEnd() ?? Data()
        guard let string = String(data: bytes, encoding: .utf8), !string.isEmpty else {
            throw ProbeError("expected nonempty UTF-8 input")
        }
        let start = DispatchTime.now().uptimeNanoseconds
        let offline = args[0] == "check-calculator"
        let host = try CalculatorHost(path: args[1], angle: angle, start: start, source: offline ? "offline-check" : "framework")
        let calculator = try ScientificCalculator(host: host, angle: angle)
        if offline {
            let data = try GeneratedContent(json: string)
            _ = try await calculator.call(arguments: data)
            return
        }

        let model = SystemLanguageModel.default
        emit(modelInfo(model))
        guard model.isAvailable, model.variant == .coreAdvanced3 else {
            throw ProbeError("requires an available on-device AFM 3 Core Advanced")
        }
        let registered = args[0] == "registered"
        let tools: [any Tool] = registered ? [calculator] : []
        let session = LanguageModelSession(model: model, tools: tools, instructions: nil as String?)
        let options = GenerationOptions(samplingMode: .greedy, maximumResponseTokens: 256, toolCallingMode: .allowed)
        emit(["event": "request", "prompt": string, "tools": tools.map(\.name),
              "sampling": "greedy", "max_response_tokens": 256, "tool_calling_mode": "allowed"])
        do {
            let response = try await session.respond(to: string, options: options)
            emit(["event": "final", "answer": response.content, "transcript": transcriptEvents(session), "elapsed_ms": elapsed(start)])
        } catch {
            emit(["event": "final", "answer": NSNull(), "transcript": transcriptEvents(session),
                  "error": error.localizedDescription, "elapsed_ms": elapsed(start)])
            exit(1)
        }
    }
}
