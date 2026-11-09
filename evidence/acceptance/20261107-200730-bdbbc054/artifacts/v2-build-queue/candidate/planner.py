"""Public incremental build planning API."""

from dependencies import required_nodes
from scheduling import schedule


def plan_build(graph, targets, cached):
    return schedule(graph, required_nodes(graph, targets, cached))
