"""Exercise CLI routing, image/text transport and over-context documents. Synthetic input only."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zlib

ROUTES=[
    ("qa-ja","本文にある窓の色を一語で答えて。","玄関は白い。窓は群青色。","answer"),
    ("qa-en","What is the lamp made of? Use an exact quote.","The lamp is made of bronze.","answer"),
    ("summary-ja","重要な原文を抜き出して要約して。","白鳥が戻った。水位は安定している。観測会を再開する。参加費は無料。","summarize"),
    ("summary-en","Select the most important sentences for a short summary.","It was cloudy. The board reopened the archive. Lending resumed. The chairs are red.","summarize"),
    ("rewrite-ja","次の連絡をやさしい日本語に言い換えて。","当該設備の稼働を一時的に見合わせます。","rewrite"),
    ("rewrite-en","Translate this message into English.","博物館は月曜休館です。","rewrite"),
    ("reason","Offer two explanations for the pattern and discuss uncertainty.","The count rose following a change in staffing.","ask"),
    ("open","Write a one-sentence story about a lighthouse.","","ask"),
]

def write_card(p):
    def chunk(k,data):
        return struct.pack(">I",len(data))+k+data+struct.pack(">I",zlib.crc32(k+data))
    n=96
    p.write_bytes(b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">2I5B",n,n,8,2,0,0,0))
        +chunk(b"IDAT",zlib.compress((b"\0"+b"\xf7\x10\x18"*n)*n))+chunk(b"IEND",b""))

def run(bin,args,stdin="",allowed=(0,3)):
    r=subprocess.run([str(bin),*args],input=stdin,text=True,capture_output=True,timeout=360)
    if r.returncode not in allowed:raise RuntimeError((args[:2],r.stdout[:180],r.stderr[:100]))
    return json.loads(r.stdout)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("binary",type=Path)
    p.add_argument("directory",type=Path)
    a=p.parse_args();d=a.directory;d.mkdir(parents=True,exist_ok=True)
    b=a.binary.resolve();rows=[]
    for id,instruction,source,expected in ROUTES:
        r=run(b,["auto",instruction],source)
        route=(r.get("result") or {}).get("route")
        rows.append(dict(id=id,expected_route=expected,route=route,passed=route==expected,response=r))
    image=d/"red-card.png";write_card(image)
    schema={"type":"object","properties":{"color":{"type":"string","enum":["red","green","blue"]},"marker":{"type":"string"}},"required":["color","marker"],"additionalProperties":False}
    spec=d/"image-schema.json";spec.write_text(json.dumps(schema))
    r=run(b,["--image",str(image),"structure",str(spec),"Read the supplied marker and identify the image's main color."],"Marker: QL-639")
    rows.append(dict(id="image-and-text",passed=r["result"]=={"color":"red","marker":"QL-639"},response=r))
    snippets=["連絡係は淡水のにおいを調べた。各項目について簡単な所見を記録して作業を終えた。","庭の定点観測を続けた。午後には風向きが変わったが、設備を置いた位置はそのまま。","軽量の試料箱を棚に戻した。回収作業では行き先を確認してから容器を引き渡した。"]
    longdoc="\n".join(snippets[i%3] for i in range(535))+"\n最終的な整理票コードは QMX-4921 と確定した。"
    q="What is the final sorting ticket code? Copy the code alone."
    for name,opts in [("long-direct",["--direct"]),("long-verified",[]),("long-call-limit",["--max-calls","1"])]:
        r=run(b,[*opts,"answer",q],longdoc)
        v=(r.get("result") or {})
        answer=(v.get("answer") or {}).get("value") if isinstance(v,dict) else None
        passed=(r["status"]=="held" and r["result"] is None) if name!="long-verified" else answer=="QMX-4921"
        rows.append(dict(id=name,passed=passed,source_sha256=hashlib.sha256(longdoc.encode()).hexdigest(),source_chars=len(longdoc),response=r))
    (d/"probes.json").write_text(json.dumps(rows,indent=2,ensure_ascii=False)+"\n")
    print([(r["id"],r["passed"]) for r in rows])
if __name__=="__main__":main()
