"""Bounded specialist prototypes. run() sees only public task input; grade() runs afterwards."""
from importlib import import_module

ARMS=("direct","current","candidate")
DOMAINS={"table":"table","translation":"translation","constraints":"constraints"}
FIXTURES={"table":"table_fixtures","translation":"translation_fixtures","constraints":"constraint_fixtures"}


def module(name):
    return import_module((__package__+"." if __package__ else "")+"domains."+name)


def cases(split):
    if split not in ("dev","test"):
        raise ValueError("split must be dev or test")
    return [c for name in FIXTURES.values() for c in module(name).make(split)]


def arms(case):
    return (*ARMS,"code") if case["group"]=="table" else ARMS


def run(case, arm, invoke):
    if arm not in arms(case):
        raise ValueError("unsupported arm")
    return module(DOMAINS[case["group"]]).run(case["job"],arm,invoke)


def grade(case, arm, response):
    return module(DOMAINS[case["group"]]).grade(case["job"],case["gold"],arm,response)
