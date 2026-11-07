"""Protected observation adapter: expected answers live outside this project."""
import copy
from report import summarize


def observe(*args):
    before = copy.deepcopy(args)
    result = summarize(*args)
    return {"result": result, "inputs_unchanged": args == before}

