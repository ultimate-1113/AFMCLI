"""Persistent native process client. Only uses the Python standard library."""
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter_ns

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FixedIndex:
    def __init__(self, binary, directory, policy):
        start = perf_counter_ns()
        self.process = subprocess.Popen([str(binary), "serve", str(directory), policy],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        text=True, bufsize=1)
        try:
            self.ready = self._read()
            if not self.ready.get("ready") or self.ready.get("policy") != policy:
                raise RuntimeError(f"Unexpected greeting {self.ready}")
        except BaseException:
            self.close()
            raise
        self.startup_wall_ns = perf_counter_ns() - start

    def _read(self):
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"placement-index closed stdout (exit {self.process.poll()})")
        result = json.loads(line)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    def call(self, request):
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        response = self._read()
        if request.get("id") != response.get("id"):
            raise RuntimeError("Mismatched JSONL request ID")
        return response

    def score(self, question, request_id):
        from rank_study import vector
        return self.call({"id": request_id, "active": vector(question, {})})

    def close(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(timeout=30)
        self.process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


if __name__ == "__main__":
    binary, directory, policy = sys.argv[1:]
    with FixedIndex(binary, directory, policy) as index:
        print(json.dumps(index.ready), flush=True)
        for line in sys.stdin:
            req = json.loads(line)
            response = index.score(req["question"], req["id"]) if "question" in req else index.call(req)
            print(json.dumps(response), flush=True)
