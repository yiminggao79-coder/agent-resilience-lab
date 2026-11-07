def dependency_map(jobs):
    """Copy dependencies; callers retain ownership of their input containers."""
    return {name: set(dependencies) for name, dependencies in jobs.items()}

