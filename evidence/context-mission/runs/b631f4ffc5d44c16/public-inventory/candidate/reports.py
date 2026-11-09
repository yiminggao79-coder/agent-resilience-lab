from allocation import allocate


def build_report(stock, orders):
    # Delegate every allocation rule (idempotent replay, atomic rejection,
    # conflict detection, input isolation) to the current allocation API.
    allocation = allocate(stock, orders)
    remaining = allocation['remaining']
    results = allocation['results']

    first_decision = {}
    rejected_ids = []
    for row in results:
        order_id = row['id']
        if order_id in first_decision:
            # Equivalent replay: counted once, consumes nothing.
            continue
        first_decision[order_id] = row['accepted']
        if not row['accepted']:
            rejected_ids.append(order_id)

    accepted_unique = 0
    rejected_unique = 0
    for accepted in first_decision.values():
        if accepted:
            accepted_unique += 1
        else:
            rejected_unique += 1

    reserved_by_sku = {
        sku: stock[sku] - remaining[sku] for sku in sorted(stock)
    }

    return {
        'remaining': remaining,
        'accepted_unique': accepted_unique,
        'rejected_unique': rejected_unique,
        'units_reserved': sum(reserved_by_sku.values()),
        'reserved_by_sku': reserved_by_sku,
        'rejected_ids': rejected_ids,
    }
