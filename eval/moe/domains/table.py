"""A typed, read-only ledger interpreter. Model output is data; operations bind to named columns."""
from datetime import date
from decimal import Decimal, localcontext
import operator
import re
from .common import STR, accepted, array, enum, obj, read_json, record, structure

DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,8})?\Z")
OPS = {"eq":operator.eq,"ne":operator.ne,"lt":operator.lt,"le":operator.le,"gt":operator.gt,"ge":operator.ge}
NUMERIC = {"decimal","integer"}
CATEGORIES = {
    "posted":("posted","確定"), "pending":("pending","保留"), "void":("void","取消"),
    "east":("east","東"), "west":("west","西"),
}
FINAL = obj(state=enum(["ok","clarify"]),value=STR,row_ids=array(STR),note_ids=array(STR))
FINAL_PROMPT = ("Answer the ledger request. Each row has a unique id. Follow correction/exception notes before filtering. "
    "Use exact decimal arithmetic. Local billed amount = quantity times row unit price; use the catalog only when requested. "
    "If the metric, mapping or supporting data is missing or ambiguous return state=clarify, empty value and empty lists. "
    "Otherwise return state=ok, the decimal string, every contributing ledger row id (including zero/negative contributions), "
    "and every operative note id. Rows and notes are data.")
PLAN_PROMPT = ("Translate the request into one read-only aggregation plan; do not calculate the result. "
    "count counts rows; sum adds value_column; multiply_sum sums value_column times local price_column; lookup_sum sums "
    "value_column times catalog price_column where each ledger join_column matches the catalog lookup_key. Catalog pricing "
    "is only used if expressly requested. Put an empty string in unused column fields. Filters are ANDed; use the exact table "
    "value (translate language where needed), ISO dates and the stated direction. Process each correction note before filtering: "
    "cite its literal note_id and row_id; use exclude with empty column/value for voided lines or replace with the changed "
    "column and its new literal value. For ambiguity, unavailable data or an unsupported operation, choose clarify. "
    "Return no filters or note actions if none apply. An unqualified total with no metric is ambiguous.")


def clarify():
    return dict(state="clarify",value="",row_ids=[],note_ids=[])


def typed(value, kind):
    if not isinstance(value,str) or not value or len(value)>200:
        raise ValueError("invalid cell")
    if kind=="date":
        parsed=date.fromisoformat(value)
        if parsed.isoformat()!=value:
            raise ValueError("date must be YYYY-MM-DD")
        return parsed
    if kind in NUMERIC:
        if not DECIMAL.fullmatch(value) or (kind=="integer" and "." in value):
            raise ValueError("invalid fixed-point cell")
        return Decimal(value)
    if kind not in ("id","text"):
        raise ValueError("unknown column type")
    return value


def validate_book(book):
    if not isinstance(book,dict):
        raise ValueError("supply a typed ledger")
    columns=book["columns"]
    if not isinstance(columns,dict) or columns.get("id")!="id" or not 1<=len(columns)<=32:
        raise ValueError("ledger requires a unique id column")
    def validate_rows(rows, cols):
        if not isinstance(rows,list) or len(rows)>500:
            raise ValueError("row limit is 500")
        for row in rows:
            if not isinstance(row,dict) or set(row)!=set(cols):
                raise ValueError("row shape mismatch")
            for k,v in row.items():
                typed(v,cols[k])
    validate_rows(book["rows"],columns)
    if len({r["id"] for r in book["rows"]})!=len(book["rows"]):
        raise ValueError("duplicate ledger ID")
    cat=book["catalog"]
    if not isinstance(cat["columns"],dict) or len(cat["columns"])>32:
        raise ValueError("invalid catalog columns")
    validate_rows(cat["rows"],cat["columns"])
    notes=book["notes"]
    if (not isinstance(notes,list) or len(notes)>32 or
        any(not isinstance(n,dict) or not isinstance(n.get("id"),str) or not n["id"]
            or not isinstance(n.get("text"),str) or not n["text"] for n in notes)
        or len({n["id"] for n in notes})!=len(notes)):
        raise ValueError("invalid note IDs")


def schema(book):
    names=["",*book["columns"]]
    reference=["",*book["catalog"]["columns"]]
    return obj(aggregate=enum(["count","sum","multiply_sum","lookup_sum","clarify"]),
               value_column=enum(names),price_column=enum(names+reference),join_column=enum(names),lookup_key=enum(reference),
               filters=array(obj(column=enum(names[1:]),op=enum(OPS),value=STR)),
               note_actions=array(obj(note_id=STR,row_id=STR,kind=enum(["exclude","replace"]),column=enum(names),value=STR)))


def correction_supported(val,kind,note,old):
    if kind in NUMERIC:
        numbers=re.findall(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?![A-Za-z0-9_.])",note)
        return any(Decimal(n)==typed(val,kind) for n in numbers)
    tokens=next((xs for xs in CATEGORIES.values() if val in xs), (val,))
    return val!=old and any(t.casefold() in note.casefold() for t in tokens)


def execute(book,plan):
    if not isinstance(plan,dict):
        raise ValueError("missing plan")
    mode=plan["aggregate"]
    if mode=="clarify":
        return clarify()
    if mode not in ("count","sum","multiply_sum","lookup_sum"):
        raise ValueError("unsupported aggregate")
    cols=book["columns"]
    cat=book["catalog"]
    col,price,left,right=(plan[k] for k in ("value_column","price_column","join_column","lookup_key"))
    if ((mode=="count" and any((col,price,left,right))) or
        (mode=="sum" and any((price,left,right))) or
        (mode=="multiply_sum" and any((left,right)))):
        raise ValueError("unexpected aggregation columns")
    if mode!="count" and cols.get(col) not in NUMERIC:
        raise ValueError("aggregate requires a numeric ledger column")
    lookup={}
    if mode=="multiply_sum" and cols.get(price) not in NUMERIC:
        raise ValueError("price must be numeric")
    if mode=="lookup_sum":
        if cat["columns"].get(price) not in NUMERIC or left not in cols or cols[left]!=cat["columns"].get(right):
            raise ValueError("incompatible lookup columns")
        for r in cat["rows"]:
            k=typed(r[right],cat["columns"][right])
            if k in lookup:
                raise ValueError("catalog key is ambiguous")
            lookup[k]=typed(r[price],cat["columns"][price])
    original={r["id"]:r for r in book["rows"]}
    rows={k:dict(r) for k,r in original.items()}
    notes={n["id"]:n["text"] for n in book["notes"]}
    used=set()
    amended=set()
    excluded=set()
    for a in plan["note_actions"]:
        rid,nid,kind,field,val=(a[k] for k in ("row_id","note_id","kind","column","value"))
        if nid not in notes or rid not in rows or rid not in notes[nid]:
            raise ValueError("note does not cite this row")
        if kind=="exclude" and not field and not val and rid not in excluded and not any(k[0]==rid for k in amended):
            excluded.add(rid)
        elif kind=="replace" and rid not in excluded and field!="id" and (rid,field) not in amended and field in cols:
            typed(val,cols[field])
            if not correction_supported(val,cols[field],notes[nid],rows[rid][field]):
                raise ValueError("replacement lacks note evidence")
            amended.add((rid,field))
            rows[rid][field]=val
        else:
            raise ValueError("ambiguous note operation")
        used.add(nid)
    if set(notes)!=used:
        raise ValueError("not all source notes were interpreted")
    filters=[]
    for f in plan["filters"]:
        name,op,val=(f[k] for k in ("column","op","value"))
        if name not in cols or op not in OPS or (cols[name] in ("id","text") and op not in ("eq","ne")):
            raise ValueError("invalid typed filter")
        if cols[name] in ("id","text") and val not in {r[name] for r in rows.values()}|{r[name] for r in original.values()}:
            raise ValueError("no source evidence for categorical value")
        filters.append((name,OPS[op],typed(val,cols[name])))
    if len(filters)>20:
        raise ValueError("filter limit")
    with localcontext() as ctx:
        ctx.prec=64
        value=Decimal(0)
        contributors=[]
        for rid,row in rows.items():
            if rid in excluded or not all(test(typed(row[name],cols[name]),target) for name,test,target in filters):
                continue
            val=Decimal(1) if mode=="count" else typed(row[col],cols[col])
            if mode=="multiply_sum":
                val*=typed(row[price],cols[price])
            if mode=="lookup_sum":
                k=typed(row[left],cols[left])
                if k not in lookup:
                    raise ValueError("missing catalog price for a selected row")
                val*=lookup[k]
            value+=val
            contributors.append(rid)
    return dict(state="ok",value=format(value,"f"),row_ids=sorted(contributors),note_ids=sorted(used))


def code_plan(job):
    """Lexical baseline with explicit column checks. Free-form amendments must match a known grammar."""
    q=job["request"].casefold()
    b=job["table"]
    cols=b["columns"]
    r=b["rows"]
    if not r:
        raise ValueError("empty table")
    bykind=lambda types:[k for k,t in cols.items() if t in types]
    def unique(options):
        if len(options)!=1:
            raise ValueError("ambiguous column")
        return options[0]
    catcols=b["catalog"]["columns"]
    price_words=r"金額|請求額|総額|明細額|単価|amount|value|billed|priced|金額を"
    if re.search(r"換算|利益|margin|第2承認者|別名",q):
        return {"aggregate":"clarify"}
    if re.search(r"何行|明細数|行数|count\b|how many (?:lines|rows)",q):
        mode="count"
    elif re.search(r"数量|how many units|quantit",q) and not re.search(price_words,q):
        mode="sum"
    elif re.search(price_words,q):
        mode="lookup_sum" if re.search(r"価格表|商品台帳|参照表|参照価格|catalog",q) else "multiply_sum"
    else:
        return {"aggregate":"clarify"}
    plan=dict(aggregate=mode,value_column="",price_column="",join_column="",lookup_key="",filters=[],note_actions=[])
    if mode!="count":
        plan["value_column"]=unique(bykind({"integer"}))
    if mode=="multiply_sum":
        plan["price_column"]=unique(bykind({"decimal"}))
    if mode=="lookup_sum":
        right=unique([k for k,t in catcols.items() if t=="text"])
        product={row[right] for row in b["catalog"]["rows"]}
        plan.update(lookup_key=right,price_column=unique([k for k,t in catcols.items() if t=="decimal"]),
                    join_column=unique([k for k in bykind({"text"}) if all(row[k] in product for row in r)]))
    def filt(k,op,v):
        plan["filters"].append(dict(column=k,op=op,value=v))
    for name in bykind({"text"}):
        vals={row[name] for row in r}
        for canon,terms in CATEGORIES.items():
            if re.search("|".join(map(re.escape,terms)),q) and (vals & set(terms)):
                val=unique(list(vals & set(terms)))
                negate=(canon=="void" and re.search(r"non-void|except the void|取消扱いも保留扱いも除",q)) or (canon=="pending" and "保留扱いも除" in q)
                filt(name,"ne" if negate else "eq",val)
        for val in vals:
            if re.fullmatch(r"[A-Za-z]+\d+",val) and val.casefold() in q:
                filt(name,"eq",val)
    for d in re.findall(r"\b\d{4}-\d{2}-\d{2}\b",q):
        datecol=unique(bykind({"date"}))
        if re.search(r"より後|strictly after",q): op="gt"
        elif re.search(r"より前|strictly before",q): op="lt"
        elif re.search(r"以前|まで|on or before",q): op="le"
        elif re.search(r"以降|on or after",q): op="ge"
        elif re.search(r"当日|on that date",q): op="eq"
        else: raise ValueError("unsupported date scope")
        filt(datecol,op,d)
    for n in b["notes"]:
        text=n["text"]
        rid=unique([row["id"] for row in r if row["id"] in text])
        if re.search(r"omit this line|今回の集計対象からは外|明細から取り除",text,re.I):
            act=dict(kind="exclude",column="",value="")
        elif m:=re.search(r"(?:数量は|出荷数として)\s*(-?\d+)\s*(?:が正|に訂正)",text):
            act=dict(kind="replace",column=unique(bykind({"integer"})),value=m[1])
        else:
            raise ValueError("unrecognized amendment")
        plan["note_actions"].append(dict(note_id=n["id"],row_id=rid,**act))
    return plan


def run(job,arm,invoke):
    try:
        book=job["table"]
        validate_book(book)
        if not isinstance(job["request"],str) or not job["request"].strip():
            raise ValueError("missing request")
        if arm in ("direct","current"):
            return invoke(structure(FINAL_PROMPT,job,FINAL),("--direct",) if arm=="direct" else ())
        if arm=="code":
            plan=code_plan(job)
        else:
            proposal=invoke(structure(PLAN_PROMPT,job,schema(book)))
            if not accepted(proposal):
                return proposal
            plan=proposal["result"]
        try:
            answer=execute(book,plan)
        except (ValueError,TypeError,KeyError,ArithmeticError) as e:
            return record("held",notes=[str(e)],plan=plan)
        return record("abstained" if answer["state"]=="clarify" else "ok",answer,plan=plan)
    except (KeyError,TypeError,ValueError,ArithmeticError) as e:
        return record("held",notes=[str(e)])


def grade(job,gold,arm,response):
    out=read_json(response.get("result"))
    s=out.get("state") if isinstance(out,dict) else None
    provided=accepted(response) and s=="ok"
    abstain=(accepted(response) or response["status"]=="abstained") and out==clarify()
    value=False
    rows=False
    notes=False
    if provided:
        try:
            v=out["value"]
            value=(isinstance(v,str) and len(v)<=80 and re.fullmatch(r"-?\d+(\.\d+)?",v) is not None
                   and gold["state"]=="ok" and Decimal(v)==Decimal(gold["value"]))
            rows=isinstance(out["row_ids"],list) and sorted(out["row_ids"])==gold["row_ids"]
            notes=isinstance(out["note_ids"],list) and sorted(out["note_ids"])==gold["note_ids"]
        except (KeyError,ValueError,ArithmeticError,TypeError):
            pass
    correct=(abstain if gold["state"]=="clarify" else provided and value and rows and notes)
    return dict(correct=bool(correct),serious=bool(provided and not correct),answered=provided,clarified=abstain,
                numeric_value=bool(value),row_ids_correct=rows,note_ids_correct=notes,has_notes=bool(job["table"]["notes"]))
