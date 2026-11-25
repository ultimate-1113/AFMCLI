"""Small adapters for the public afmcli JSON protocol."""
import json
import re


def compact(value):
    return json.dumps(value,ensure_ascii=False,separators=(",",":"))


def obj(**fields):
    return {"type":"object","properties":fields,"required":list(fields),"additionalProperties":False}


def enum(values):
    return {"type":"string","enum":list(dict.fromkeys(values))}


def array(item):
    return {"type":"array","items":item}


STR = {"type":"string"}
INT = {"type":"integer"}


def read_json(value):
    if not isinstance(value,str):
        return value
    s = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```",s,re.I|re.S)
    try:
        return json.loads(fenced[1] if fenced else s,
                          object_pairs_hook=_distinct,
                          parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except (ValueError,TypeError,RecursionError):
        return None


def _distinct(pairs):
    out={}
    for k,v in pairs:
        if k in out:
            raise ValueError("duplicate key")
        out[k]=v
    return out


def structure(instruction, source, schema):
    return dict(task="structure",instruction=instruction,source=compact(source),schema=schema)


def record(status, result=None, notes=(), **diagnostics):
    out=dict(status=status,result=result,notes=list(notes))
    if diagnostics:
        out["diagnostics"]=diagnostics
    return out


def accepted(r):
    return r.get("status") in ("ok","review","unverified")
