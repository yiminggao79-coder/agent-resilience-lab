from graph import dependency_map
from waves import group_ranks


def build_batches(jobs, completed=None):
    """Return dependency batches and names that cannot be scheduled."""
    raise NotImplementedError("batch planning has not been implemented")

