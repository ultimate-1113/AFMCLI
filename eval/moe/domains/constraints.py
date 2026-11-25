"""Verify a proposed bounded integer interpretation against independent readings of concrete cases."""
from collections import defaultdict
from hashlib import sha256
from itertools import product
import math
import operator
from .common import accepted, compact, enum, obj, read_json, record, structure

REL={"lt":operator.lt,"le":operator.le,"eq":operator.eq,"ge":operator.ge,"gt":operator.gt}
DOMAIN_LIMIT=4096
POINT_LIMIT=14
DIRECT=("Interpret this bounded integer linear problem, solve it, and return only one JSON object. If it lacks a "
    "sufficient domain, objective or priority return {\"state\":\"clarify\"}. Otherwise return an object with keys "
    "interpretation and solution. interpretation has variables [{name,min,max}] (in passage order), "
    "constraints [{weights,relation,rhs}] (relation lt/le/eq/ge/gt), and objectives [{weights,sense}] (min/max in "
    "priority order); every coefficient array uses variable order. solution has feasible (boolean), assignment "
    "(object with each variable, null if infeasible), objective_values (integer array, empty if infeasible), "
    "and optimal_count (number tied before the final lexicographically smallest assignment, zero if infeasible).")
JUDGE=("Use the original passage to judge each concrete example independently. For p-items label legal if the named "
    "assignment satisfies every domain and restriction, illegal if it violates any, otherwise unclear. A feasible "
    "assignment need not be optimal. For q-items compare two legal assignments under the stated objective priorities. "
    "Use left/right for the preferred side and tie if the objectives do not distinguish them; do not apply arbitrary "
    "assignment order. Use unclear for an absent or ambiguous rule. Use only the passage and the supplied examples.")


def integer(x):
    return type(x) is int and abs(x)<=10**9


def dot(weights,x):
    return sum(a*b for a,b in zip(weights,x))


class Model:
    def __init__(self,p):
        variables=p["variables"]
        self.rows=p["constraints"]
        self.goals=p["objectives"]
        if not isinstance(variables,list) or not 1<=len(variables)<=8:
            raise ValueError("invalid variable count")
        if not isinstance(self.rows,list) or len(self.rows)>32 or not isinstance(self.goals,list) or not 1<=len(self.goals)<=8:
            raise ValueError("invalid declaration count")
        self.names=[]
        self.bounds=[]
        for v in variables:
            n,lo,hi=v["name"],v["min"],v["max"]
            if (not isinstance(n,str) or not n.strip() or n!=n.strip() or n in self.names or
                    not integer(lo) or not integer(hi) or hi<lo):
                raise ValueError("invalid variable or domain")
            self.names.append(n)
            self.bounds.append((lo,hi))
        if math.prod(hi-lo+1 for lo,hi in self.bounds)>DOMAIN_LIMIT:
            raise ValueError("domain exceeds the specialist's 4096-assignment limit")
        for r in self.rows+self.goals:
            if not isinstance(r["weights"],list) or len(r["weights"])!=len(self.names) or not all(map(integer,r["weights"])):
                raise ValueError("invalid coefficient vector")
        if any(r["relation"] not in REL or not integer(r["rhs"]) for r in self.rows):
            raise ValueError("invalid relation")
        if any(r["sense"] not in ("min","max") for r in self.goals):
            raise ValueError("invalid objective")
        self.points=list(product(*(range(lo,hi+1) for lo,hi in self.bounds)))
        self.legal={x for x in self.points if all(self.test(r,x) for r in self.rows)}
        self.ranks={x:self.rank(x) for x in self.legal}

    def test(self,row,x):
        return REL[row["relation"]](dot(row["weights"],x),row["rhs"])

    def rank(self,x):
        return tuple(dot(o["weights"],x)*(1 if o["sense"]=="min" else -1) for o in self.goals)

    def assign(self,x):
        return dict(zip(self.names,x))

    def canonical(self,x):
        return tuple(sorted(self.assign(x).items()))

    def solution(self):
        if not self.legal:
            return dict(feasible=False,assignment=None,objective_values=[],optimal_count=0)
        x=min(self.legal,key=lambda k:(self.ranks[k],k))
        return dict(feasible=True,assignment=self.assign(x),objective_values=[dot(g["weights"],x) for g in self.goals],
                    optimal_count=sum(rank==self.ranks[x] for rank in self.ranks.values()))

    def ordered(self):
        levels=defaultdict(set)
        for x,rank in self.ranks.items():
            levels[rank].add(self.canonical(x))
        return [levels[k] for k in sorted(levels)]


def encoded(value):
    return compact(canonicalize(value))


def canonicalize(value):
    if isinstance(value,dict):
        return {k:canonicalize(value[k]) for k in sorted(value)}
    if isinstance(value,list):
        return [canonicalize(v) for v in value]
    return value


def sample(model,source):
    def stable(x):
        return sha256((source+"|"+str(x)).encode()).digest()
    pts=sorted(model.points,key=stable)
    selected=[]
    def add(x):
        if x not in selected:
            selected.append(x)
    # Meet each declaration from both sides, seeking a witness satisfying all other declarations.
    for i,row in enumerate(model.rows):
        for polarity in (True,False):
            pool=[x for x in pts if model.test(row,x)==polarity]
            if pool:
                def distance(x):
                    elsewhere=sum(not model.test(r,x) for j,r in enumerate(model.rows) if i!=j)
                    return elsewhere,abs(dot(row["weights"],x)-row["rhs"]),stable(x)
                add(min(pool,key=distance))
    if len(selected)>POINT_LIMIT:
        raise ValueError("too many constraint boundaries to review")
    if model.legal:
        add(min(model.legal,key=lambda x:(model.ranks[x],x)))
    # Verify both domain boundaries independently of restrictions and include at least one ordinary interior point.
    pivot=min(model.legal,key=stable) if model.legal else pts[0]
    for i,(lo,hi) in enumerate(model.bounds):
        for v in (lo-1,hi+1):
            x=list(pivot)
            x[i]=v
            add(tuple(x))
    if len(selected)>POINT_LIMIT:
        raise ValueError("too many domain boundaries to review")
    for x in [pivot,pts[0],pts[-1]]:
        if len(selected)<POINT_LIMIT:
            add(x)
    selected.sort(key=stable)
    examples={"p"+str(i+1):model.assign(x) for i,x in enumerate(selected)}
    expected={k:("legal" if x in model.legal else "illegal") for k,x in zip(examples,selected)}
    ranks=defaultdict(list)
    for x in sorted(model.legal,key=stable):
        ranks[model.ranks[x]].append(x)
    levels=sorted(ranks)
    pairs=[]
    if levels:
        tie=next((members for level in levels if len(members:=ranks[level])>1),None)
        if tie:
            pairs.append((tie[0],tie[1]))
        if len(levels)>1:
            pairs.append((ranks[levels[0]][0],ranks[levels[1]][0]))
        # Check secondary priority within a primary tie as well as across primary values.
        for i in range(1,len(model.goals)+1):
            alternatives=next(((l,r) for l,r in zip(levels,levels[1:]) if l[:i-1]==r[:i-1] and l[i-1]!=r[i-1]),None)
            if alternatives:
                l,r=alternatives
                pair=(ranks[l][0],ranks[r][0])
                if pair not in pairs:
                    pairs.append(pair)
    comparisons={}
    for i,(a,b) in enumerate(pairs):
        if stable(a)>stable(b):
            a,b=b,a
        k="q"+str(i+1)
        comparisons[k]={"left":model.assign(a),"right":model.assign(b)}
        ar,br=model.ranks[a],model.ranks[b]
        expected[k]="tie" if ar==br else ("left" if ar<br else "right")
    schema=obj(**{k:enum(["legal","illegal","unclear"] if k in examples else ["left","right","tie","unclear"]) for k in expected})
    return examples,comparisons,expected,schema


def run(job,arm,invoke):
    source=job.get("source")
    if not isinstance(source,str) or not source.strip():
        return record("held",notes=["missing source problem"])
    if arm=="direct":
        return invoke(dict(task="ask",instruction=DIRECT,source=source),("--direct",))
    proposal=invoke(dict(task="solve",source=source),("--decompose",) if arm=="candidate" else ())
    if arm=="current" or proposal["status"]!="review":
        return proposal
    value=proposal["result"]
    try:
        model=Model(value["interpretation"])
        if encoded(model.solution())!=encoded(value["solution"]):
            raise ValueError("reported solution differs from exhaustive evaluation of proposed model")
        examples,pairs,expected,schema=sample(model,source)
    except (ValueError,TypeError,KeyError,ArithmeticError) as e:
        return record("held",notes=[str(e)],proposal=value)
    vote=invoke(structure(JUDGE,dict(original=source,feasibility_examples=examples,objective_comparisons=pairs),schema),("--repairs","0"))
    info=dict(proposal=value,examples=examples,comparisons=pairs,predicted=expected,judgments=vote.get("result"))
    if not accepted(vote):
        return record(vote["status"],notes=vote.get("notes",[]),**info)
    if vote["result"]!=expected:
        return record("held",notes=["independent passage reading did not support all boundary or preference judgments"],**info)
    return record("review",value,["checked sampled assignments and preferences against an independent reading"],**info)


def grade(job,gold,arm,response):
    value=read_json(response.get("result"))
    report=accepted(response) and isinstance(value,dict)
    clarify=response["status"]=="abstained" or (report and value=={"state":"clarify"})
    published=report and not clarify and ("interpretation" in value or "solution" in value)
    detail=response.get("diagnostics",{})
    predicted,judge=detail.get("predicted",{}),detail.get("judgments")
    matches={k:isinstance(judge,dict) and judge.get(k)==v for k,v in predicted.items()}
    boundary=dict(points_checked=sum(k.startswith("p") for k in predicted),
                  point_agreement=sum(ok for k,ok in matches.items() if k.startswith("p")),
                  preferences_checked=sum(k.startswith("q") for k in predicted),
                  preference_agreement=sum(ok for k,ok in matches.items() if k.startswith("q")))
    result=dict(correct=False,serious=False,answered=bool(published),clarified=bool(clarify),exact_solution=False,
                legal_assignments=False,preference_order=False,admitted_illegal=0,rejected_legal=0,optimal_count=False,**boundary)
    if gold is None:
        result.update(correct=bool(clarify),serious=bool(published))
        return result
    expected=Model(gold)
    want=expected.solution()
    if not published:
        return result
    solution=value.get("solution")
    result["exact_solution"]=encoded(solution)==encoded(want)
    if isinstance(solution,dict):
        result["optimal_count"]=type(solution.get("optimal_count")) is int and solution["optimal_count"]==want["optimal_count"]
    legal={expected.canonical(x) for x in expected.legal}
    try:
        candidate=Model(value["interpretation"])
        output={candidate.canonical(x) for x in candidate.legal}
        result["legal_assignments"]=set(candidate.names)==set(expected.names) and legal==output
        result["preference_order"]=result["legal_assignments"] and expected.ordered()==candidate.ordered()
        result["admitted_illegal"]=len(output-legal)
        result["rejected_legal"]=len(legal-output)
    except (TypeError,ValueError,KeyError,ArithmeticError):
        pass
    result["correct"]=result["exact_solution"] and result["legal_assignments"] and result["preference_order"]
    result["serious"]=not result["exact_solution"] or result["admitted_illegal"]>0
    return result
