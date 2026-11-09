def group_ranks(ranks):
    if not ranks:
        return []
    return [sorted(name for name, rank in ranks.items() if rank == level)
            for level in range(max(ranks.values()) + 1)]

