"""Measure and replay what an on-device model retains between independent sessions."""
import argparse
from collections import Counter
from contextlib import nullcontext
import fcntl
import json
from pathlib import Path
from statistics import median
import subprocess
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/"mode_improvements"))
import study as primary
from eval.combinations import backend
from eval.combinations.study import save_json
from eval.handoff import panel

ARMS=("history","previous","reopen")
CAP=6200
MAX_NOTES=160
MAX_IDS=3
REOPEN_BYTES=1200


def obj(properties):
    return {"type":"object","properties":properties,"required":list(properties),"additionalProperties":False}


TEXT={"type":"string"}
MEMO=obj({"memo":TEXT,"refs":{"type":"array","items":TEXT}})
FINAL=obj({"state":{"type":"string","enum":["確定","未確定"]},"value":TEXT,"evidence_id":TEXT})


def known_case():
    case=next(c for c in primary.cases("qa","test") if c["id"]=="qa-test-18-a")
    case=primary.panel("qa").followup(case)
    lines=case["job"]["source"].splitlines(keepends=True)
    rows=[]
    mapping=[]
    offset=0
    for i,line in enumerate(lines,1):
        ident=f"L{i:03}"
        end=offset+len(line.encode())
        rows.append({"id":ident,"text":line.rstrip("\r\n")})
        mapping.append((offset,end,ident))
        offset=end
    packets=[rows[i*len(rows)//6:(i+1)*len(rows)//6] for i in range(6)]
    # Original offsets are used only when scoring the final answer.
    evidence=[ident for a,b,ident in mapping for span in case["gold"]["spans"]
              if a<=span["start_byte"]<span["end_byte"]<=b]
    primary.require(bool(evidence),"original spans could not be mapped")
    return dict(id="known-qa-18-a-followup",size="known",kind="previous_context_error",
                goal=case["job"]["question"],packets=packets,gold={"value":case["gold"]["value"],"evidence":evidence})


def corpus():
    cases=list(panel.cases())
    primary.require(len(cases)==8 and len({c["id"] for c in cases})==8,"incorrect pilot panel")
    for size in ("short","long"):
        group=[c for c in cases if c["size"]==size]
        primary.require(len(group)==4 and sum(c["gold"]["value"] is not None for c in group)==2,"unbalanced pilot panel")
    for c in cases:
        entries=[r for packet in c["packets"] for r in packet]
        keys=[r["id"] for r in entries]
        primary.require(len(c["packets"])==3 and all(c["packets"]) and len(set(keys))==len(keys),
                        "packet or source ID collision")
        primary.require(c["gold"]["evidence"] and set(c["gold"]["evidence"])<=set(keys),"missing evidence")
    return cases+[known_case()]


def fingerprint(cases):
    files=[HERE/"protocol.md",HERE/"panel.py",HERE/"handoff.py",HERE/"source-tokens.json",HERE.parent/"combinations"/"backend.py"]
    return {"files":{f.relative_to(HERE.parent.parent).as_posix():primary.sha(f.read_bytes()) for f in files},
            "primary_lock":primary.lock_check(),"cases":{c["id"]:primary.sha(primary.encode(c)) for c in cases}}


def render(rows):
    return "\n".join(row["id"]+" "+row["text"] for row in rows)


def pick_refs(values, visible):
    if not isinstance(values,list):
        return []
    selected=[]
    for value in values:
        if isinstance(value,str) and value in visible and value not in selected:
            selected.append(value)
    return selected[:MAX_IDS]


def prompt_for(case,arm,index,memos):
    packets=case["packets"]
    current=packets[index]
    last=index+1==len(packets)
    instruction=("最終回答を作る。資料で確定した場合は短い値だけを原文の表記で返す。"
                 "撤回後に後継がない場合と適用対象を選べない場合は、未確定・空の値を返す。"
                 "どちらでも現在の状態を裏付ける原文のIDを evidence_id に一つ返す。") if last else (
                 "次の段階に引き継ぐ。目的に関わる現行の値、対象・条件、変更や未確定の理由を短く memo にまとめる。"
                 "原文を裏付けるIDを refs に最大3件コピーする。撤回・訂正があれば古い候補を区別する。")
    remaining=(f"今回を含めて残り{len(packets)-index}区間。最後に目的に答えて原文IDを示す。")
    chunks=["目的\n"+case["goal"],"残りタスク\n"+remaining+"\n"+instruction]
    visible={r["id"] for r in current}
    if index:
        earlier=[r for packet in packets[:index] for r in packet]
        if arm=="history":
            chunks+=["既読の全原文\n"+render(earlier),"これまでの出力\n"+primary.encode(memos).decode()]
            visible.update(r["id"] for r in earlier)
        else:
            prev=memos[-1]
            chunks.append("直前の出力\n"+primary.encode(prev).decode())
            visible.update(prev["refs"])
            if arm=="reopen":
                originals={r["id"]:r for r in earlier}
                rows=[]
                used=0
                for ident in prev["refs"]:
                    row=originals.get(ident)
                    if row is not None:
                        n=len((render([row])+"\n").encode())
                        if used+n<=REOPEN_BYTES:
                            rows.append(row)
                            used+=n
                if rows:
                    chunks.append("直前の出力が参照した原文\n"+render(rows))
    chunks.append("今回新たに届いた原文\n"+render(current))
    return "\n\n".join(chunks),visible


def execute(case,arm,archive=None):
    io=backend.IO(archive=archive)
    memos=[]
    final=None
    status="invalid_output"
    for index in range(len(case["packets"])):
        is_final=index==len(case["packets"])-1
        prompt,visible=prompt_for(case,arm,index,memos)
        out=io.read(prompt,FINAL if is_final else MEMO,max_input=CAP)
        if out is None:
            status=io.last_read_status
            break
        if is_final:
            if (out.get("state") in ("確定","未確定") and isinstance(out.get("value"),str)
                    and isinstance(out.get("evidence_id"),str)):
                final={"state":out["state"],"value":out["value"].strip(),"evidence_id":out["evidence_id"].strip()}
                if final["evidence_id"] not in visible:
                    status="unknown_source_id"
                else:
                    status="completed"
            break
        if not isinstance(out.get("memo"),str) or not isinstance(out.get("refs"),list):
            break
        memo=" ".join(out["memo"].split())[:MAX_NOTES]
        memos.append({"memo":memo,"refs":pick_refs(out["refs"],visible)})
    if archive is not None:
        primary.require(len(io.events)==len(archive),"unused archived calls")
    gold=case["gold"]
    returned=bool(final and final["value"])
    value_ok=bool(final and (
        (gold["value"] is None and final["state"]=="未確定" and not returned) or
        (gold["value"] is not None and final["state"]=="確定" and final["value"]==gold["value"])))
    evidence_ok=bool(status=="completed" and final["evidence_id"] in gold["evidence"])
    sent=[e["raw"]["input_tokens"] for e in io.events if e["raw"]["sent"]]
    return {"status":status,"final":final,"memos":memos,"value_correct":value_ok,
            "evidence_correct":evidence_ok,"correct":value_ok and evidence_ok,
            "unsupported":gold["value"] is None and returned,"sent_tokens":sum(sent),
            "peak_sent":max(sent,default=0),"generations":len(sent),
            "elapsed_ms":round(sum(e["elapsed_ms"] for e in io.events),3),"events":io.events}


def schedule(cases):
    for i,c in enumerate(cases):
        for arm in ARMS[i%3:]+ARMS[:i%3]:
            yield c,arm


def summary(rows):
    def aggregate(items):
        return {"n":len(items),"completed":sum(r["status"]=="completed" for r in items),
                "values":sum(r["value_correct"] for r in items),"strict":sum(r["correct"] for r in items),
                "unsupported":sum(r["unsupported"] for r in items),"statuses":dict(Counter(r["status"] for r in items)),
                "total_tokens":sum(r["sent_tokens"] for r in items),"peak_sent":max((r["peak_sent"] for r in items),default=0),
                "total_generations":sum(r["generations"] for r in items),
                "median_ms":median([r["elapsed_ms"] for r in items]) if items else None}
    complete={r["id"] for r in rows if r["size"]!="known"}
    for r in rows:
        if r["status"]!="completed":
            complete.discard(r["id"])
    groups={"new":lambda r:r["size"]!="known","short":lambda r:r["size"]=="short","long":lambda r:r["size"]=="long",
            "new_common_completions":lambda r:r["id"] in complete,"known":lambda r:r["size"]=="known"}
    return {group:{a:aggregate([r for r in rows if r["arm"]==a and accept(r)]) for a in ARMS}
            for group,accept in groups.items()}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("sizes","freeze","run","verify"))
    p.add_argument("--out",type=Path,default=HERE/"results")
    p.add_argument("--doctor",type=Path,help="AFMCLI executable for hardware preflight")
    args=p.parse_args()
    cases=corpus()
    if args.action=="sizes":
        with primary.system_lock():
            result={}
            for case in cases:
                packets=[render(p) for p in case["packets"]]
                result[case["id"]]={"size":case["size"],"full":backend.count("\n".join(packets)),
                                     "packets":[backend.count(p) for p in packets]}
        save_json(HERE/"source-tokens.json",result)
        print({k:[v["full"] for v in result.values() if v["size"]==k] for k in ("short","long","known")});return
    lock=fingerprint(cases)
    if args.action=="freeze":
        with (HERE/"lock.json").open("x",encoding="utf-8") as f:
            json.dump(lock,f,ensure_ascii=False,sort_keys=True,indent=2);f.write("\n")
        print(primary.sha(primary.encode(lock)));return
    primary.require(json.loads((HERE/"lock.json").read_bytes())==lock,"handoff study fingerprint differs")
    running=args.action=="run"
    if running:
        if not args.doctor:
            p.error("run requires --doctor")
        pre=json.loads(subprocess.check_output([str(args.doctor.resolve()),"doctor"],timeout=45))
        primary.require(pre["available"] and pre["target"]==primary.MODEL and pre["route"]=="system","unexpected model")
        args.out.mkdir(parents=True,exist_ok=True)
    with (args.out/".writer.lock").open("a") if running else nullcontext() as lease:
        if running:
            fcntl.flock(lease,fcntl.LOCK_EX)
        run_panel(args.out,cases,running)


def run_panel(out,cases,running):
    path=out/"observations.json"
    rows=json.loads(path.read_bytes()) if path.exists() else []
    previous={(r["id"],r["arm"]):r for r in rows}
    primary.require(len(previous)==len(rows),"duplicate observations")
    ordered=[]
    for c,arm in schedule(cases):
        key=(c["id"],arm)
        if key in previous:
            obs=previous[key]
            value=execute(c,arm,archive=obs["events"])
        else:
            primary.require(running,"missing observation")
            with primary.system_lock():
                value=execute(c,arm)
        record={"id":c["id"],"size":c["size"],"arm":arm,**value}
        ordered.append(record)
        if key in previous:
            primary.require(record==obs,"archived observation did not replay")
        else:
            primary.require(len(ordered)>len(rows),"incompatible prior schedule")
            save_json(path,ordered)
            print(c["id"],arm,value["status"],value["sent_tokens"],flush=True)
    primary.require(len(ordered)==len(list(schedule(cases))) and (running or rows==ordered),"unexpected observations")
    result=summary(ordered)
    if running:
        save_json(out/"summary.json",result)
    else:
        primary.require(result==json.loads((out/"summary.json").read_bytes()),"different handoff summary")
    print(json.dumps(result["new"],ensure_ascii=False))


if __name__=="__main__":
    main()
