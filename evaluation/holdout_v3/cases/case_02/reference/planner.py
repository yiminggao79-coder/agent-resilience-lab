from graph import dependency_map
from waves import group_ranks


def build_batches(jobs, completed=None):
    dependencies = dependency_map(jobs)
    finished = set(completed or [])
    pending = set(jobs) - finished
    ranks = {}
    level = 0
    while pending:
        ready = sorted(name for name in pending if dependencies[name] <= finished)
        if not ready:
            break
        for name in ready:
            ranks[name] = level
        pending.difference_update(ready)
        finished.update(ready)
        level += 1
    return {"batches": group_ranks(ranks), "blocked": sorted(pending)}

