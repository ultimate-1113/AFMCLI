"""Targeted repair against public translation obligations, with independent held-out meaning rubrics."""
from collections import Counter
import re
import unicodedata
from .common import accepted, compact, record

NUMBER = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
NUMBERS = re.compile(NUMBER)
JA = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
LANG = {"en":"English","ja":"Japanese"}


def norm(s):
    return unicodedata.normalize("NFKC",s).replace("−","-")


def token_pattern(term):
    t=norm(term)
    left=r"(?<![A-Za-z])" if re.match(r"[A-Za-z]",t) else ""
    right=r"(?![A-Za-z])" if re.search(r"[A-Za-z]$",t) else ""
    return left+re.escape(t)+right


def count_token(term,text,case=True):
    return len(re.findall(token_pattern(term),norm(text),0 if case else re.I))


def split_output(text,n):
    if not isinstance(text,str) or not text.strip():
        return []
    lines=[line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines)==n:
        return lines
    return [s.strip() for s in re.split(r"(?<=[。！？])\s*|(?<=[.!?])\s+(?=\S)"," ".join(lines)) if s.strip()]


def validate(job):
    source,target=job["source_language"],job["target_language"]
    if {source,target}!={"ja","en"}:
        raise ValueError("use the en/ja language pair")
    lines=job["sentences"]
    if not isinstance(lines,list) or not 1<=len(lines)<=48 or any(not isinstance(s,str) or not s.strip() or "\n" in s for s in lines):
        raise ValueError("supply one nonempty source sentence per item")
    if not isinstance(job["glossary"],dict):
        raise ValueError("glossary must map source terms to required target expressions")
    terms=[*job["glossary"].keys(),*job["glossary"].values()]
    for k in ("names","units"):
        if not isinstance(job[k],list):
            raise ValueError("names and units must be lists")
        terms.extend(job[k])
    if any(not isinstance(s,str) or not s.strip() for s in terms) or len(terms)>256:
        raise ValueError("invalid glossary, name, or unit")


def public_checks(job,src,out):
    """One check per independent public obligation, with number/unit bindings in source order."""
    text=norm(out)
    original=norm(src)
    direction=bool(JA.search(text)) if job["target_language"]=="ja" else bool(re.search("[a-zA-Z]{2}",text)) and not JA.search(text)
    result=dict(direction=bool(direction),numbers=Counter(NUMBERS.findall(original))==Counter(NUMBERS.findall(text)))
    for name in sorted(set(job["names"])):
        want=count_token(name,src)
        seen=count_token(name,out)
        if want or seen:
            result["name "+name]=want==seen
    for source_term,target_term in job["glossary"].items():
        want=count_token(source_term,src,False)
        seen=count_token(target_term,out,False)
        if want or seen:
            result["term "+target_term]=want==seen
    for unit in sorted(set(job["units"]),key=lambda u:(-len(u),u)):
        pattern=rf"({NUMBER})\s*{re.escape(norm(unit))}"+(r"(?![A-Za-z])" if re.search(r"[A-Za-z]$",unit) else "")
        want=Counter(re.findall(pattern,original))
        seen=Counter(re.findall(pattern,text))
        if want or seen:
            result["unit "+unit]=want==seen and count_token(unit,src)==count_token(unit,out)
    return result


def instruction(job):
    return (f"Translate every source sentence from {LANG[job['source_language']]} to {LANG[job['target_language']]}. "
        "Return only the translations, one sentence per original line in the original order. Preserve obligations, direction "
        "of comparisons, negation and timing. Keep each number as written, its unit symbol and exact proper name. "
        "Use the supplied target glossary terms literally (English capitalization may follow context). "
        +compact({k:job[k] for k in ("glossary","names","units")}))


def draft_job(job):
    return dict(task="rewrite",instruction=instruction(job),source="\n".join(job["sentences"]),keep_numbers=True,require_change=True)


def repair_job(job,index,original,bad,violations):
    glossary={s:t for s,t in job["glossary"].items() if count_token(s,original,False) or count_token(t,bad,False)}
    terms=dict(glossary=glossary,names=[n for n in job["names"] if count_token(n,original) or count_token(n,bad)],
               units=[u for u in job["units"] if count_token(u,original) or count_token(u,bad)])
    return dict(task="ask",instruction=f"Correct this single {LANG[job['target_language']]} translation against its original. "
                "Return only the corrected sentence. Apply required terminology, number-unit pairs and names; preserve "
                "negation, timing and comparison direction. Do not add commentary.",
                source=compact(dict(original=original,draft=bad,failed_checks=violations,**terms)))


def run(job,arm,invoke):
    try:
        validate(job)
    except (ValueError,KeyError,TypeError) as e:
        return record("held",notes=[str(e)])
    if arm in ("direct","current"):
        return invoke(draft_job(job),("--direct",) if arm=="direct" else ())
    raw=invoke(draft_job(job),("--repairs","0"))
    draft=raw.get("result")
    if not accepted(raw):
        attempts=raw.get("attempts",[])
        if (raw.get("status")=="held" and attempts and attempts[-1].get("validation_error")
                and isinstance(attempts[-1].get("response"),str)):
            draft=attempts[-1]["response"]
        else:
            return raw
    original=job["sentences"]
    lines=split_output(draft,len(original))
    if len(lines)!=len(original):
        return record("held",notes=["source and draft sentences cannot be aligned"],draft=draft,attempted=lines,repair_index=None)
    broken=[[k for k,ok in public_checks(job,s,t).items() if not ok] for s,t in zip(original,lines)]
    idx=next((i for i,issues in enumerate(broken) if issues),None)
    if idx is not None:
        patch=invoke(repair_job(job,idx,original[idx],lines[idx],broken[idx]),("--repairs","0"))
        if not accepted(patch):
            return record(patch["status"],notes=patch.get("notes",[]),draft=draft,attempted=lines,repair_index=idx,initial_violations=broken)
        patched=split_output(patch.get("result"),1)
        if len(patched)!=1 or "\n" in patch["result"].strip():
            return record("held",notes=["repair did not return one line"],draft=draft,attempted=lines,repair_index=idx,initial_violations=broken)
        lines[idx]=patched[0]
    left=[[k for k,ok in public_checks(job,s,t).items() if not ok] for s,t in zip(original,lines)]
    details=dict(draft=draft,attempted=lines,repair_index=idx,initial_violations=broken,residual_violations=left)
    if any(left):
        return record("held",notes=["numeric, language, name, unit or glossary checks unresolved"],**details)
    return record("ok","\n".join(lines),**details)


# Independent meaning rubrics. No reference translation or rubric enters either generation request.
LEVEL_PATTERNS={
    "max":(r"at most|no (?:more|greater|higher) than|(?:must |shall |does |do )?not (?:exceed|surpass|be (?:more|greater|higher) than)|or (?:less|lower)|up to|a maximum of|capp?ed at|upper limit|limit.{0,80}? to\b",
           r"以下|以内|最大|上限|超え(?:ない|ては(?:いけ|なら)ない)|超過(?:しない|不可)|まで.{0,5}制限"),
    "min":(r"at least|no (?:less|lower|fewer) than|(?:must |shall |does |do )?not (?:fall|drop|go) below|must not be (?:less|lower) than|or (?:more|higher|greater)|a minimum of|lower limit",
           r"以上|最低|下限|少なくとも|下回(?:らない|ってはならない)|割り込まない"),
    "lt":(r"less than|(?:strictly )?(?:below|under|lower than)|must not reach",r"未満|より(?:も)?(?:低|小さ|少な)|下回|満たない|届かない"),
    "gt":(r"more than|greater than|higher than|(?:strictly )?(?:exceed|above|over|surpass)",r"超え|超過|上回|より(?:も)?(?:高|大き|多)"),
    "eq":(r"exactly|precisely|equal to|\bsets?\b.{0,100}?\b(?:to|at)\b|fixed at",r"ちょうど|丁度|正確に|ぴったり|に設定|に固定|に等し"),
    "up":(r"increas|rais(?:e|ed)|boost|rose|rises?|grew|expand",r"引き上げ|増[加やえ]|上昇|上げ"),
    "down":(r"reduc|decreas|lower(?:ed|s)?|fell|fall|drop|cut",r"引き下げ|低下|減[少らっ]|下げ"),
    "approx":(r"approximately|about|around|roughly|estimated (?:at|to be)|approximately",r"およそ|約|前後|程度|おおむね|概算"),
}
ACT_PATTERNS={
    "keep":(r"keep|retain|stor(?:e|age)|hold|save|preserve",r"保管|保持|保存|取ってお|維持"),
    "discard":(r"discard|dispos|throw (?:away|out)|remove|scrap",r"廃棄|処分|捨て"),
    "open":(r"\bopen|unseal|unlock",r"開け|開く|開放|開封"),
    "close":(r"\bclos(?:e|ing)|\bshut|\bseal",r"閉じ|閉め|閉鎖|密閉|封鎖"),
    "seal":(r"\bseal|airtight|unopened",r"密封|密閉|封印|未開封"),
    "defer":(r"postpon|defer|delay|put off|reschedul",r"延期|遅らせ|先送り|繰り下げ|延ば"),
    "early":(r"\bstart|\bbegin|initiat|commence|launch",r"始め|開始|着手|起動"),
}
PHASE={"keep":"inspect","discard":"inspect","never":"deliver","before":"depart","after":"arrive","remain":"pack","defer":"inspect","early":"inspect"}
PHASE_PATTERN={"inspect":(r"inspect|examin|audit|check",r"検査|点検|検品|審査|確認"),
               "deliver":(r"deliver|receipt|receiv",r"納品|配達|配送|受[け取領]|引[き渡]"),
               "depart":(r"depart|leav|ship(?:ment|ping)",r"出発|出航|出荷|発送"),
               "arrive":(r"arriv|reach|receipt|receiv",r"到着|着荷|着いた|受領"),
               "pack":(r"pack|wrap",r"梱包|包装|荷造")}
TIME={"before":(r"\bbefore\b|\bprior\b|in advance",r"前|先立"),
      "after":(r"\bafter\b|following|\bfrom\b|subsequent",r"後|以降|から|終わ")}
NEG=(r"\b(?:not|never|avoid|prohibit(?:ed)?|forbid(?:den)?|without)\b|mustn.t|don't|cannot|can't",
     r"ない|禁止|不可|いけません|ならない|せず|ことなく|ません")


def match(pattern,text):
    return re.search(pattern,text,re.I) is not None


def meaning_checks(job,gold,lines):
    i=0 if job["target_language"]=="en" else 1
    first,second=(norm(s).casefold() for s in lines)
    kind,action=gold["level"],gold["action"]
    # Match multiword inclusive expressions first so that 'must not exceed' never becomes strict 'exceed'.
    found=set()
    scan=first
    for key in ("max","min"):
        pat=LEVEL_PATTERNS[key][i]
        if match(pat,scan):
            found.add(key)
            scan=re.sub(pat," ",scan,flags=re.I)
    for key in ("lt","gt","eq","up","down","approx"):
        if match(LEVEL_PATTERNS[key][i],scan):
            found.add(key)
    direction_groups={"max","min","lt","gt","eq","approx"} if kind not in {"up","down"} else {"up","down"}
    same=kind in found and not (found & (direction_groups-{kind}))
    if kind in {"up","down"} and same:
        if i==0:
            same=match(r"\bby\b|(?:increase|decrease|reduction|rise|drop|gain) of|[\d.]+\s*\S+\s+(?:increase|decrease|reduction)",first)
        else:
            same=not match(r"(?:まで|に)(?:引き)?(?:上げ|下げ|増[加や]|減[少ら])",first)
    opposite=bool(found & (direction_groups-{kind})) and not same
    wanted={"never":"open","before":"close","after":"open","remain":"seal"}.get(action,action)
    verb={k:match(p[i],second) for k,p in ACT_PATTERNS.items()}
    neg=match(NEG[i],second)
    if action=="never":
        actok=(verb["open"] and neg) or ((verb["seal"] or verb["close"]) and not neg)
        badverb=verb["open"] and not neg and not verb["seal"] and not verb["close"]
    else:
        # Japanese obligation morphology しなければならない / してください is affirmative.
        if i==1 and match(r"なければならない|なくてはならない|ねばならない",second):
            neg=False
        actok=verb[wanted] and not neg
        opposite_verbs={"keep":["discard"],"discard":["keep"],"close":["open"],"open":["close","seal"],
                        "seal":["open"],"defer":["early"],"early":["defer"]}[wanted]
        badverb=(neg and verb[wanted]) or (not verb[wanted] and any(verb[v] for v in opposite_verbs))
    time="before" if action in ("before","early") else "after"
    other="after" if time=="before" else "before"
    tk=match(TIME[time][i],second)
    reversed_time=match(TIME[other][i],second) and not tk
    phase=match(PHASE_PATTERN[PHASE[action]][i],second)
    return {"S1/meaning":bool(same),"S2/action":bool(actok),"S2/timing":bool(tk and not reversed_time),"S2/event":bool(phase)}, bool(opposite or badverb or reversed_time)


def checks(job,gold,lines):
    if len(lines)!=len(job["sentences"]):
        return {},False
    items={}
    for i,(src,out) in enumerate(zip(job["sentences"],lines),1):
        for k,v in public_checks(job,src,out).items():
            items[f"S{i}/{k}"]=v
    meaning,opposed=meaning_checks(job,gold,lines)
    return dict(items,**meaning),opposed


def grade(job,gold,arm,response):
    diag=response.get("diagnostics",{})
    value=split_output(response.get("result"),len(job["sentences"]))
    delivered=accepted(response) and bool(value)
    attempted=value if delivered else diag.get("attempted",[])
    after,opposed=checks(job,gold,attempted)
    draft=split_output(diag.get("draft"),len(job["sentences"])) if "draft" in diag else attempted
    before,_=checks(job,gold,draft)
    common=set(before)&set(after)
    fixed=sum(not before[k] and after[k] for k in common)
    damaged=sum(before[k] and not after[k] for k in common)
    safe=lambda prefix:bool(after) and all(v for k,v in after.items() if prefix in k)
    critical=not (safe("/numbers") and safe("/unit ") and safe("/name "))
    correct=bool(delivered and after and all(after.values()))
    return dict(correct=correct,serious=bool(delivered and (critical or opposed)),answered=bool(delivered),
                units_fixed=fixed,good_units_broken=damaged,repair_attempted=diag.get("repair_index") is not None,
                numeric_preservation=safe("/numbers"),unit_preservation=safe("/unit "),name_preservation=safe("/name "),
                term_preservation=safe("/term "),meaning_preservation=safe("/meaning") and safe("/action") and safe("/timing") and safe("/event"),
                semantic_polarity_error=opposed,correct_fixed_sentences=sum(
                    bool(before) and bool(after) and any(not v for k,v in before.items() if k.startswith(f"S{i}/"))
                    and all(v for k,v in after.items() if k.startswith(f"S{i}/")) for i in (1,2)),
                rubric_units_passed=sum(after.values()),rubric_units=len(after))
