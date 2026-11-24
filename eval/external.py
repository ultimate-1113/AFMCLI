"""Public independent probes. Only IDs and per-case scores are published."""
import hashlib
import json
from pathlib import Path

SOURCES = {
    "jsquad": ("https://raw.githubusercontent.com/yahoojapan/JGLUE/6f071c09316baae89c3d083a90985b4b1cb9968c/datasets/jsquad-v1.3/valid-v1.3.json", "fb0a57a35281ff03bbe7f0e878cff5b577985fbe6fcb242e7e8ddb63d1c0ab11"),
    "jnli": ("https://raw.githubusercontent.com/yahoojapan/JGLUE/6f071c09316baae89c3d083a90985b4b1cb9968c/datasets/jnli-v1.3/valid-v1.3.json", "ca0353efc7c2eebfb6de4e13f16295053c8b1ee65e7b0849190c90426fbc495f"),
    "squad": ("https://raw.githubusercontent.com/rajpurkar/SQuAD-explorer/eee5fdbf62f8613a7812b03419e6b29617b74fd1/dataset/dev-v2.0.json", "80a5225e94905956a6446d296ca1093975c4d3b3260f1d6c8f68bc2ab77182d8"),
}
SEED = "afmcli-v0.1-independent-2026-11"

def choose(rows,count,used):
    by_id = lambda r: hashlib.sha256((SEED + ":" + r["id"]).encode()).digest()
    kept=[]
    for row in sorted(rows,key=by_id):
        if row["group"] not in used:
            used.add(row["group"])
            kept.append(row)
            if len(kept)==count:
                return kept
    raise ValueError("too few independent source groups")

def fixtures(directory):
    result=[]
    for name,(_,checksum) in SOURCES.items():
        raw=(Path(directory)/(name+".json")).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=checksum:
            raise ValueError("incorrect source file for "+name)
        used=set()
        if name=="jnli":
            options=[dict(name=k,description=d) for k,d in [
                ("entailment","仮説は前提が真なら必ず真"),("contradiction","仮説は前提と両立しない"),("neutral","前提だけでは仮説の真偽は未確定")]]
            grouped={x["name"]:[] for x in options}
            for x in map(json.loads,raw.splitlines()):
                job=dict(task="classify",labels=options,source="前提: "+x["sentence1"]+"\n仮説: "+x["sentence2"])
                row=dict(id=name+"-"+str(x["sentence_pair_id"]),group=x["yjcaptions_id"].split("-")[0],job=job,gold=x["label"],dataset=name)
                grouped[x["label"]].append(row)
            for pool in grouped.values():
                result.extend(choose(pool,5,used))
        else:
            pools={True:[],False:[]}
            for article in json.loads(raw)["data"]:
                for paragraph in article["paragraphs"]:
                    for q in paragraph["qas"]:
                        vals=list(dict.fromkeys(a["text"] for a in q["answers"]))
                        job=dict(task="answer",question=q["question"],source=paragraph["context"])
                        row=dict(id=name+"-"+q["id"],group=article["title"],job=job,gold=vals,dataset=name)
                        pools[bool(vals)].append(row)
            result.extend(choose(pools[True],16 if name=="jsquad" else 8,used))
            if name=="squad":
                result.extend(choose(pools[False],8,used))
    return result

if __name__=="__main__":
    import argparse
    import urllib.request
    p=argparse.ArgumentParser(description="Fetch the pinned public development sets for independent evaluation.")
    p.add_argument("directory",type=Path)
    d=p.parse_args().directory
    d.mkdir(parents=True,exist_ok=True)
    for name,(url,digest) in SOURCES.items():
        with urllib.request.urlopen(url,timeout=60) as response:
            data=response.read()
        if hashlib.sha256(data).hexdigest()!=digest:
            raise SystemExit("dataset digest mismatch: "+name)
        (d/(name+".json")).write_bytes(data)
    print("Selected",len(fixtures(d)),"examples")
