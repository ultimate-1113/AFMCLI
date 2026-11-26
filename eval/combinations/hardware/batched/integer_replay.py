"""Recompute all archived CPU/MPS scores from the text with independent integer arithmetic."""
from array import array
import argparse
import hashlib
from pathlib import Path
import sys

if __package__:
    from . import survey
else:
    import survey


def check(raw):
    _,_,docs,queries=survey.data.features()
    expected={(r["ready"]["m"],r["ready"]["q"]):r["ready"]["proof"] for r in raw["measurements"]}
    shapes=survey.data.SHAPES
    covered=0
    for bank in range(2):
        # A 16-bit lane holds a sum bounded by 1024, so neighbouring query
        # results cannot carry into one another. Each document adds only its
        # active feature columns. This avoids floating-point GEMM entirely.
        columns=[0]*survey.data.K
        for j,row in enumerate(queries[bank*survey.data.BANK:(bank+1)*survey.data.BANK]):
            lane=1<<(16*j)
            for feature in row:
                columns[feature] |= lane
        digests={shape:(hashlib.sha256(),hashlib.sha256()) for shape in shapes}
        for i,doc in enumerate(docs):
            scores=sum(columns[f] for f in doc).to_bytes(2*survey.data.BANK,"little")
            integers=array("H")
            integers.frombytes(scores)
            if sys.byteorder!="little":
                integers.byteswap()
            floats=array("f",integers)
            if sys.byteorder!="little":
                floats.byteswap()
            f32=floats.tobytes()
            for shape,(h16,h32) in digests.items():
                m,q=shape
                if i<m:
                    h16.update(scores[:q*2]);h32.update(f32[:q*4]);covered+=q
        for shape,(h16,h32) in digests.items():
            p=expected[shape][bank]
            survey.need((h16.hexdigest(),h32.hexdigest())==(p["u16_sha256"],p["sha256"]),
                        f"independent full matrix disagrees for {shape} bank {bank}")
    return {"independent_exact_cells":covered,"shapes":len(shapes),"banks":2,
            "checked":"all canonical uint16 and binary32 outputs against independent per-query integer lanes"}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    result=check(survey.load(args.out/"survey.json"))
    proof=args.out/"full-proof.json"
    if proof.exists():
        survey.need(survey.load(proof)==result, "archived full integer proof differs")
    else:
        survey.save(proof,result)
    print(result)
