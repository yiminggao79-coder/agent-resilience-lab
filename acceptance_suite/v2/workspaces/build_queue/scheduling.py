"""Schedule a required dependency subgraph."""


def schedule(graph, needed):
    indegree = {node: sum(dep in needed for dep in graph[node])
                for node in needed}
    dependents = {node: set() for node in needed}
    for node in needed:
        for dep in graph[node]:
            if dep in needed:
                dependents[dep].add(node)
    ready = sorted(node for node in needed if indegree[node] == 0)
    order = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for dependent in sorted(dependents[node]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    return {"order": order, "blocked": sorted(needed.difference(order))}
