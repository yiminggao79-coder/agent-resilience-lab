from allocation import allocate


def build_report(stock, orders):
    """Summarize an allocation without mutating the caller's stock or orders."""
    result = allocate(stock, orders)
    remaining = dict(result['remaining'])

    accepted_unique = 0
    rejected_ids = []
    seen = set()
    for row in result['results']:
        order_id = row['id']
        if order_id in seen:
            continue
        seen.add(order_id)
        if row['accepted']:
            accepted_unique += 1
        else:
            rejected_ids.append(order_id)

    reserved_by_sku = {}
    units_reserved = 0
    for sku in sorted(stock):
        consumed = stock[sku] - remaining[sku]
        reserved_by_sku[sku] = consumed
        units_reserved += consumed

    return {
        'remaining': remaining,
        'accepted_unique': accepted_unique,
        'rejected_unique': len(rejected_ids),
        'units_reserved': units_reserved,
        'reserved_by_sku': reserved_by_sku,
        'rejected_ids': rejected_ids,
    }
