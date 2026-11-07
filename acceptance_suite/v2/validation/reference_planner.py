"""Independent acceptance reference; never copied into an evaluation workspace."""


def plan_build(graph, targets, cached):
    satisfied = set(cached)
    required = set()
    stack = list(targets)
    while stack:
        node = stack.pop()
        if node in satisfied or node in required:
            continue
        required.add(node)
        stack.extend(graph[node])
    pending = set(required)
    order = []
    while pending:
        candidates = [node for node in pending
                      if set(graph[node]).issubset(satisfied)]
        if not candidates:
            break
        node = min(candidates)
        order.append(node)
        satisfied.add(node)
        pending.remove(node)
    return {"order": order, "blocked": sorted(pending)}
