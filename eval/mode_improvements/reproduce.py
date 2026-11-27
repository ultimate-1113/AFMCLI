"""Rerun development and held-out comparisons in a user-chosen work directory on an AFM Mac."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import select_methods
import study


def command(args):
    subprocess.run([str(x) for x in args],check=True)


def checkout(root, destination, revision):
    if destination.exists():
        head=subprocess.check_output(["git","-C",destination,"rev-parse","HEAD"],text=True).strip()
        edits=subprocess.check_output(["git","-C",destination,"status","--porcelain","--untracked-files=no"])
        study.require(head==revision and not edits,"existing source tree differs from the pinned revision")
    else:
        command(["git","-C",root,"worktree","add","--detach",destination,revision])


def build(tree, target, destination, flags):
    args=["cargo","build","--release","--locked","--no-default-features",
          "--manifest-path",tree/"Cargo.toml","--target-dir",target]
    if flags:
        args.extend(["--features",",".join(flags)])
    command(args)
    executable=target/"release"/"afmcli"
    if destination.exists():
        study.require(study.sha(destination.read_bytes())==study.sha(executable.read_bytes()),
                      "new build differs from existing trial executable; use another work directory")
    else:
        temp=destination.with_suffix(".pending")
        shutil.copy2(executable,temp)
        os.replace(temp,destination)


def run(workspace):
    root=study.HERE.parents[1]
    pinned=json.loads((study.HERE/"results"/"selection.json").read_text())["candidate_commit"]
    bin_dir=workspace/"bin"
    bin_dir.mkdir(parents=True,exist_ok=True)
    results=workspace/"results"
    baseline,candidate=workspace/"baseline-source",workspace/"candidate-source"
    checkout(root,baseline,study.BASE)
    checkout(root,candidate,pinned)
    base=bin_dir/"base"
    target=workspace/"target"
    build(baseline,target,base,[])
    exp=[sys.executable,"-B",study.HERE/"study.py"]
    select=[sys.executable,"-B",study.HERE/"select_methods.py","--results",results]
    def measure(track,phase,arms):
        args=[*exp,"run","--split",phase,"--out",results/phase,"--track",track]
        for name,exe in arms:
            args.extend(["--arm",name+"="+str(exe)])
        command(args)
    for track,features in (("qa",["trial-qa-preview","trial-qa-lookup","trial-qa-cautious"]),
                           ("calc",["trial-calculation"])):
        exe=bin_dir/("dev-"+track)
        build(candidate,target,exe,features)
        measure(track,"dev",[("base",base),("candidate",exe)])
    arms=[("base",base)]
    for method in ("ids","protection","both"):
        exe=bin_dir/("dev-mt-"+method)
        variant={m:method for m in ("draft","checked")}
        features=[flag for flag in select_methods.flags(variant) if flag.startswith(("trial-draft-","trial-checked-"))]
        build(candidate,target,exe,features)
        arms.append((method,exe))
    measure("translation","dev",arms)
    selected=subprocess.check_output([str(x) for x in [*select,"--cargo-features"]],text=True).strip().split(",")
    test=bin_dir/"candidate"
    build(candidate,target,test,selected)
    if not (results/"selection.json").exists():
        command([*select,"--base",base,"--candidate",test,"--source",pinned])
    for track in study.MODES:
        measure(track,"test",[("base",base),("candidate",test)])
        command([*exp,"decide","--track",track,"--out",results/"test"])


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work",type=Path,required=True)
    run(parser.parse_args().work.resolve())
