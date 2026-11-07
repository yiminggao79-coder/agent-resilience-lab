"""Collect nodes needed by requested targets."""


def required_nodes(graph, targets, cached):
    needed = set()
    pending = list(targets)
    while pending:
        node = pending.pop()
        if node in needed:
            continue
        needed.add(node)
        pending.extend(graph[node])
    return needed.difference(cached)
