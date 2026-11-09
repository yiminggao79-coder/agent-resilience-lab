from allocation import allocate


def build_report(stock, orders):
    result = allocate(stock, orders)
    remaining = result['remaining']

    decisions = {}
    rejected_ids = []
    for row in result['results']:
        order_id = row['id']
        if order_id in decisions:
            continue
        decisions[order_id] = row['accepted']
        if not row['accepted']:
            rejected_ids.append(order_id)

    reserved_by_sku = {}
    for sku in sorted(stock):
        reserved_by_sku[sku] = stock[sku] - remaining[sku]

    return {
        'remaining': remaining,
        'accepted_unique': sum(1 for accepted in decisions.values() if accepted),
        'rejected_unique': len(rejected_ids),
        'units_reserved': sum(reserved_by_sku.values()),
        'reserved_by_sku': reserved_by_sku,
        'rejected_ids': rejected_ids,
    }
