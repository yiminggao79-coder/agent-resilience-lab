"""Protected observation adapter: expected answers live outside this project."""
import copy
from planner import build_batches


def observe(*args):
    before = copy.deepcopy(args)
    result = build_batches(*args)
    return {"result": result, "inputs_unchanged": args == before}

