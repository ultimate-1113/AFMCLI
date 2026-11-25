"""Re-score or rerun the isolated current-candidate verification experiment."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent


def decision(method, observation, count):
    if observation["status"] != "completed":
        return None
    value = observation["value"]
    if method == "current":
        flag = value.get("supported")
        return flag if isinstance(flag,bool) else None
    effects = value.get("effects")
    if not isinstance(effects,list) or len(effects)!=count:
        return None
    active = False
    for event in effects:
        if event == "confirm":
            active = True
        elif event == "invalidate":
            active = False
        elif event != "neutral":
            return None
    return active


def compact(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))


def generate(config, method, case):
    definition = config["methods"][method]
    material = dict(excerpts=case["excerpts"])
    payload = dict(question=case["question"],candidate=case["candidate"],material=material)
    with TemporaryDirectory() as temp:
        path = Path(temp)
        schema = path/"schema.json"
        transcript = path/"response.json"
        schema.write_text(compact(definition["schema"]))
        run = subprocess.run(["/usr/bin/fm","respond","--model","system","--greedy","--no-stream",
                              "--instructions",config["system_instructions"],"--save-transcript",str(transcript),"--schema",str(schema)],
                             input=definition["instruction"]+"\n"+compact(payload),text=True,capture_output=True,timeout=45)
        if run.returncode:
            if any(w in run.stderr.lower() for w in ("refus","guardrail","safety")):
                return dict(status="refused",value=None)
            raise RuntimeError(f"fm returned exit code {run.returncode} for {case['id']}")
        t = json.loads(transcript.read_text())
        last = t["transcript"]["transcript"]["entries"][-1]
        assert t["modelName"]=="system" and last["role"] in ("response","assistant")
        assert last.get("finishReason","contentCompleted")=="contentCompleted"
        part = last["contents"][0]
        value = part["structure"]["content"] if part["type"]=="structure" else json.loads(part["text"])
        return dict(status="completed",value=value)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action",choices=["run","verify"])
    ap.add_argument("--binary",type=Path,help="AFMCLI executable for checking model availability")
    ap.add_argument("--output",type=Path,help="write a new result file for a rerun")
    args = ap.parse_args()
    data = json.loads((HERE/"verifier-probe.json").read_text())
    if args.action=="run":
        if not args.binary or not args.output or args.output.exists():
            ap.error("run requires --binary and a new --output path")
        model=json.loads(subprocess.check_output([args.binary.resolve(),"doctor"]))
        assert model["available"] and model["route"]=="system" and model["target"]=="AFM 3 Core Advanced"
    scores={method:0 for method in data["methods"]}
    for case in data["cases"]:
        for method in sorted(scores,key=lambda m:hashlib.sha256((case["id"]+m).encode()).digest()):
            if args.action=="run":
                obs=generate(data,method,case)
                obs["decision"]=decision(method,obs,len(case["excerpts"]))
                obs["correct"]=obs["decision"] is case["gold"]
                case["observations"][method]=obs
            else:
                obs=case["observations"][method]
                assert obs["decision"] is decision(method,obs,len(case["excerpts"]))
                assert obs["correct"] is (obs["decision"] is case["gold"])
            if case["id"]!="diagnostic-elm":
                scores[method]+=obs["correct"]
    if args.action=="run":
        with args.output.open("x") as out:
            json.dump(data,out,indent=2,ensure_ascii=False)
            out.write("\n")
    print(scores,"out of",len(data["cases"])-1,"contrast cases")


if __name__=="__main__":
    main()
