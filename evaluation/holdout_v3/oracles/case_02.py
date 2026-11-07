"""External oracle: recursive ancestry/rank resolution, not batch elimination."""
import copy
import random


def expected(jobs, completed=None):
    done = set(completed or [])
    cache = {}
    def rank(name, ancestors):
        if name in done:
            return -1
        if name not in jobs or name in ancestors:
            return None
        if name in cache:
            return cache[name]
        dependencies = [rank(dep, ancestors | {name}) for dep in set(jobs[name])]
        if any(value is None for value in dependencies):
            cache[name] = None
        else:
            cache[name] = 1 + max(dependencies, default=-1)
        return cache[name]
    resolved = {name:rank(name,set()) for name in jobs if name not in done}
    levels = sorted({value for value in resolved.values() if value is not None})
    return {"batches":[sorted(name for name,value in resolved.items() if value == level)
                       for level in levels],
            "blocked":sorted(name for name,value in resolved.items() if value is None)}


def cases():
    result = [
        [{},None],
        [{"a":[],"b":["a"],"c":["a"],"d":["b","c"]},None],
        [{"c":["b"],"b":["a"],"a":[]},["a"]],
        [{"x":["missing"],"y":["x"],"free":[]},None],
        [{"a":["b"],"b":["a"],"c":["b"],"d":[]},[]],
        [{"self":["self"],"next":["self"]},None],
        [{"a":["outside","outside"],"b":["a"]},["outside","outside"]],
        [{"a":["b"],"b":["a"],"c":["b"]},["a"]],
        [{"z":[],"a":[]},["z","a","irrelevant"]],
        [{"z":["root"],"b":[],"root":[],"a":[]},[]],
    ]
    rng=random.Random(91801)
    for _ in range(22):
        names=["task"+str(index) for index in range(rng.randrange(1,8))]
        jobs={}
        for name in reversed(names):
            pool=names+["external"]
            jobs[name]=[rng.choice(pool) for _ in range(rng.randrange(0,4))]
        done=[name for name in names+["external"] if rng.randrange(5)==0]
        result.append([jobs,done])
    return copy.deepcopy(result)

