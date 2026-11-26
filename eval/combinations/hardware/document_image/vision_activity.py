"""Observe host-wide GPU and CPU during matched text and image AFM streams."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import math
from pathlib import Path
import platform
import re
from statistics import median
import subprocess
import sys
import tempfile
import threading
import time

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("document_form_study",HERE/"study.py")
forms=importlib.util.module_from_spec(spec)
spec.loader.exec_module(forms)
sys.path.insert(0,str(HERE.parent/"activity"))
import activity

need,sha=activity.need,forms.sha
SIZE={"small":forms.SIZES[0],"large":forms.SIZES[-1]}
PROMPTS={
    "explain": "If a scanned form is attached, begin with its visible two-digit header number, then describe the layout and ruled fields. If no form is attached, start with 'No image' and explain how you would inspect such a form. In either case write 90 to 120 English words, using full sentences.",
    "number": "If a scanned form is attached, return just the visible two-digit header number. If no form is attached, return just 'unavailable'.",
}
# Vary condition positions across rounds. Each condition occurs exactly once in each round.
ORDERS=(
    ("idle","explain-text","number-large","explain-small","number-text","explain-large","number-small"),
    ("number-text","explain-large","number-small","idle","explain-text","number-large","explain-small"),
    ("explain-small","number-large","explain-text","number-small","explain-large","number-text","idle"),
)
POLL=0.1
IDLE=3.0
SWIFT=r'''
import CryptoKit
import Foundation
import FoundationModels

func digest(_ bytes: Data) -> String {
    SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
}
enum Failure: Error { case unavailable, inconsistentInput, empty }
@main @available(macOS 27.0, *)
struct VisionStream {
    static func main() async throws {
        let model=SystemLanguageModel.default
        guard model.isAvailable && model.variant == .coreAdvanced3 else { throw Failure.unavailable }
        let input=FileHandle.standardInput.readDataToEndOfFile()
        guard let text=String(data:input,encoding:.utf8), !text.isEmpty else { throw Failure.inconsistentInput }
        let imageURL=CommandLine.arguments.count==2 ? URL(fileURLWithPath:CommandLine.arguments[1]) : nil
        let requestedHash=try imageURL.map { try digest(Data(contentsOf:$0)) }
        let constructed=DispatchTime.now().uptimeNanoseconds
        let prompt=Prompt { if let imageURL { Attachment(imageURL:imageURL) }; text }
        let session=LanguageModelSession(model:model)
        let options=GenerationOptions(samplingMode:.greedy,maximumResponseTokens:256)
        let start=DispatchTime.now().uptimeNanoseconds
        var first: UInt64?
        var content=""
        for try await part in session.streamResponse(to:prompt,options:options) {
            if first==nil && !part.content.isEmpty { first=DispatchTime.now().uptimeNanoseconds }
            content=part.content
        }
        let end=DispatchTime.now().uptimeNanoseconds
        guard let first, !content.isEmpty else { throw Failure.empty }
        var texts=[String](), images=[[String:Any]]()
        var prompts=0
        for entry in session.transcript {
            guard case .prompt(let p)=entry else { continue }
            prompts += 1
            for seg in p.segments {
                if case .text(let t)=seg { texts.append(t.content) }
                if case .attachment(let a)=seg, case .image(let im)=a.content {
                    let pixel=im.cgImage
                    var row:[String:Any]=["width":pixel.width,"height":pixel.height]
                    if let url=im.url { row["transcript_png_sha256"]=try digest(Data(contentsOf:url)) }
                    images.append(row)
                }
            }
        }
        guard prompts==1 && texts==[text] && images.count==(imageURL==nil ? 0:1) else { throw Failure.inconsistentInput }
        var row:[String:Any]=["variant":model.variant.displayName,"prompt_sha256":digest(input),
                             "constructed":constructed,"start":start,"first":first,"end":end,"content":content,
                             "text_tokens":try await model.tokenCount(for:text),
                             "output_tokens":try await model.tokenCount(for:content),"transcript_images":images]
        if let requestedHash { row["request_png_sha256"]=requestedHash }
        let data=try JSONSerialization.data(withJSONObject:row,options:[.sortedKeys])
        try FileHandle.standardOutput.write(contentsOf:data+Data([10]))
    }
}
'''


def write(path,obj,pretty=False):
    path.write_text(json.dumps(obj,sort_keys=True,ensure_ascii=False,
                               indent=2 if pretty else None,separators=None if pretty else (",",":"))+"\n",encoding="utf-8")


def build(scratch):
    src=scratch/"VisionStream.swift"
    binary=scratch/"vision-stream"
    src.write_text(SWIFT,encoding="utf-8")
    subprocess.run(["xcrun","swiftc","-O","-parse-as-library","-target","arm64-apple-macos27.0",
                    "-module-cache-path",str(scratch/"SwiftModules"),str(src),"-o",str(binary)],check=True)
    return binary


def image_files(binary,directory,baseline):
    paths,metadata={},{}
    for name,size in SIZE.items():
        worker=forms.Machine(binary,size)
        raw=directory/(name+".raw")
        try:
            r=worker.run("vimage",True,1,dump=str(raw))
            forms.check_frames(r,worker.ready)
        finally:
            worker.close()
        pixels=raw.read_bytes()
        reference=next(x["ready"]["reference_sha256"] for x in baseline["survey"] if (x["ready"]["width"],x["ready"]["height"])==size)
        need(sha(pixels)==reference,"form changed")
        path=directory/(name+".png")
        path.write_bytes(forms.png(pixels,*size))
        paths[name]=path
        metadata[name]={"size":list(size),"pixel_sha256":reference,"png_sha256":sha(path.read_bytes())}
    return paths,metadata


def request(binary,text,image):
    args=[str(binary)]+([str(image)] if image else [])
    p=subprocess.run(args,input=text.encode(),capture_output=True,timeout=75)
    if p.returncode:
        raise RuntimeError("image stream failed (%d): %s"%(p.returncode,p.stderr.decode(errors="replace")[:1100]))
    return json.loads(p.stdout)


def block(binary,paths,sequence,answers):
    epoch=activity.clock_ns()
    stop=threading.Event()
    phases=[]
    activity.PERIOD=POLL
    with ThreadPoolExecutor(max_workers=1) as worker:
        recorder=worker.submit(activity.samples,stop,epoch)
        try:
            for key in sequence:
                row={"condition":key,"begin":activity.clock_ns()-epoch}
                if key=="idle":
                    time.sleep(IDLE)
                else:
                    task,form=key.split("-")
                    result=request(binary,PROMPTS[task],paths.get(form))
                    content=result.pop("content")
                    digest=sha(content.encode())
                    answers[digest]=content
                    for n in ("constructed","start","first","end"):
                        result[n]-=epoch
                    row["stream"]=dict(result,answer_sha256=digest)
                row["end"]=activity.clock_ns()-epoch
                phases.append(row)
        finally:
            stop.set()
        samples=recorder.result()
    need(len(samples)>1,"sampler did not run")
    first=samples[0][3]
    for item in samples:
        item[3]=[(v-initial)%(2**32) for v,initial in zip(item[3],first)]
    return {"phases":phases,"samples":samples}


def distribution(values):
    if not values:
        return {"n":0}
    v=sorted(values)
    return {"n":len(v),"median":round(median(v),2),"mean":round(sum(v)/len(v),2),
            "p95":round(v[math.ceil(len(v)*.95)-1],2),"max":round(v[-1],2)}


def summarize(data):
    need(data["sample_target_ms"]==100 and data["gpu_gauge"]==activity.GPU_KEY and len(data["rounds"])==3,"unexpected protocol")
    groups,measures={},{}
    intervals=[]; sample_cost=[]; point_gaps=[]
    referenced=set()
    for n,(seq,run) in enumerate(zip(ORDERS,data["rounds"])):
        need([p["condition"] for p in run["phases"]]==list(seq),"changed crossover")
        rows=run["samples"]
        observations=[]
        for a,b,v,c in rows:
            need(0<=a<=b and 0<=v<=100 and len(c)==4,"invalid gauge reading")
            sample_cost.append((b-a)/1e6)
        for prev,cur in zip(rows,rows[1:]):
            need(prev[1]<=cur[0],"sampling clock disorder")
            delta=[(v-p)%(2**32) for v,p in zip(cur[3],prev[3])]
            cpu=100*(sum(delta)-delta[2])/sum(delta) if sum(delta) else None
            observations.append((prev[0],cur[1],cur[2],cpu))
            point_gaps.append((cur[0]-prev[0])/1e6)
        for p in run["phases"]:
            key=p["condition"]
            marks={}
            if key=="idle":
                marks["idle"]=(p["begin"],p["end"])
            else:
                task,form=key.split("-")
                s=p["stream"]
                need(p["begin"]<=s["constructed"]<=s["start"]<s["first"]<=s["end"]<=p["end"] and s["variant"]==activity.MODEL
                     and s["prompt_sha256"]==sha(PROMPTS[task].encode()) and s["output_tokens"]>0,"invalid stream")
                img=s["transcript_images"]
                if form=="text":
                    need(not img and "request_png_sha256" not in s,"unexpected image")
                else:
                    meta=data["forms"][form]
                    need(s["request_png_sha256"]==meta["png_sha256"] and len(img)==1 and
                         [img[0]["width"],img[0]["height"]]==meta["size"],"missing transcript form")
                    if "transcript_png_sha256" in img[0]:
                        need(img[0]["transcript_png_sha256"]==meta["png_sha256"],"changed transcript PNG")
                digest=s["answer_sha256"]
                content=data["answers"][digest]
                need(sha(content.encode())==digest,"answer changed")
                referenced.add(digest)
                marks[key]=(s["start"],s["end"])
                marks[key+"-to-first"]=(s["start"],s["first"])
                marks[key+"-after-first"]=(s["first"],s["end"])
                m=measures.setdefault(key,[])
                m.append({"stream_ms":round((s["end"]-s["start"])/1e6,2),"to_first_ms":round((s["first"]-s["start"])/1e6,2),
                          "words":len(content.split()),"output_tokens":s["output_tokens"],
                          "reads_42":bool(re.search(r"(?<![0-9])42(?![0-9])",content)) if form!="text" else None})
            for label,(a,b) in marks.items():
                values=[(g,c) for x,y,g,c in observations if a<=x<=y<=b]
                groups.setdefault(label,[]).append(values)
                intervals.append({"round":n+1,"label":label,"ms":round((b-a)/1e6,2),"samples":len(values)})
    need(referenced==set(data["answers"]),"unexpected response")
    stats={}
    for name,rounds in groups.items():
        stats[name]={"gpu_percent":distribution([g for r in rounds for g,_ in r]),
                     "cpu_host_percent":distribution([c for r in rounds for _,c in r if c is not None]),
                     "samples_by_round":[len(r) for r in rounds]}
    return {"conditions":stats,"responses":measures,"intervals":intervals,"gauge_poll_gap_ms":distribution(point_gaps),
            "gauge_read_ms":distribution(sample_cost),"ane_utilization_percent":None}


def capture(args):
    baseline=json.loads((HERE/"results/observations.json").read_bytes())
    binary=build(args.scratch)
    data={"sample_target_ms":100,"gpu_gauge":activity.GPU_KEY,"answers":{},"rounds":[],
          "model":activity.MODEL,"probe_source_sha256":sha(SWIFT.encode()),
          "activity_source_sha256":sha(Path(activity.__file__).read_bytes()),"probe_binary_sha256":sha(binary.read_bytes()),
          "form_generator_sha256":sha(args.filter.read_bytes()),"os":platform.mac_ver()[0],
          "os_build":subprocess.check_output(["sw_vers","-buildVersion"],text=True).strip(),
          "chip":subprocess.check_output(["/usr/sbin/sysctl","-n","machdep.cpu.brand_string"],text=True).strip()}
    with tempfile.TemporaryDirectory(prefix="vision-activity-",dir=args.scratch) as tmp:
        with activity.system_lock():
            paths,data["forms"]=image_files(args.filter,Path(tmp),baseline)
            data["ane_published_utilization_keys"]=activity.ane_inventory()
            # Warm up both the Foundation Models service and image path before counters start.
            request(binary,PROMPTS["number"],paths["small"])
        for i,seq in enumerate(ORDERS,1):
            with activity.system_lock():
                r=block(binary,paths,seq,data["answers"])
            data["rounds"].append(r)
            print("Round",i,"complete,",len(r["samples"]),"host samples",flush=True)
    summary=summarize(data)
    write(args.raw,data)
    write(args.summary,summary,True)
    verify(args)


def verify(args):
    raw=json.loads(args.raw.read_bytes())
    need(raw["probe_source_sha256"]==sha(SWIFT.encode()) and raw["activity_source_sha256"]==sha(Path(activity.__file__).read_bytes())
         and raw["model"]==activity.MODEL,"changed probe or sampler")
    baseline=json.loads((HERE/"results/observations.json").read_bytes())
    for name,size in SIZE.items():
        digest=next(x["ready"]["reference_sha256"] for x in baseline["survey"] if (x["ready"]["width"],x["ready"]["height"])==size)
        need(raw["forms"][name]["pixel_sha256"]==digest,"unrecognized form")
    need(json.loads(args.summary.read_bytes())==summarize(raw),"replay disagrees")
    print("Verified 18 AFM streams, image attachments, three idle windows and all sample intersections.")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest="action",required=True)
    live=subs.add_parser("capture")
    live.add_argument("--filter",type=Path,required=True)
    live.add_argument("--scratch",type=Path,required=True)
    for sub in (live,subs.add_parser("verify")):
        sub.add_argument("--raw",type=Path,required=True)
        sub.add_argument("--summary",type=Path,required=True)
    args=parser.parse_args()
    if args.action=="capture":
        need(not args.raw.exists() and not args.summary.exists(),"choose fresh result paths")
        args.scratch.mkdir(parents=True,exist_ok=True)
        capture(args)
    else:
        verify(args)
