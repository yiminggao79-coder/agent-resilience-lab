from graph import dependency_map
from waves import group_ranks


def build_batches(jobs, completed=None):
    """Return dependency batches and names that cannot be scheduled."""
    dependencies = dependency_map(jobs)
    finished = set(completed) if completed is not None else set()

    remaining = [name for name in dependencies if name not in finished]
    satisfied = set(finished)

    batches = []
    while remaining:
        ready = sorted(
            name for name in remaining
            if dependencies[name] <= satisfied
        )
        if not ready:
            break
        batches.append(ready)
        satisfied.update(ready)
        ready_set = set(ready)
        remaining = [name for name in remaining if name not in ready_set]

    blocked = sorted(remaining)
    return {"batches": batches, "blocked": blocked}
