from allocation import allocate


def build_report(stock, orders):
    """Summarize an allocation run without duplicating allocation logic.

    Delegates entirely to :func:`allocation.allocate`, so all existing rules
    (atomic rejection, order-preserving processing, per-call idempotency,
    non-mutation) apply. Returns exactly:

    - ``remaining``: stock after allocation (all original keys kept);
    - ``accepted_unique`` / ``rejected_unique``: distinct order-id counts by
      the id's first accepted/rejected decision;
    - ``units_reserved``: total units consumed across all stock keys;
    - ``reserved_by_sku``: every original stock key mapped to units consumed,
      in sorted key order (zeros included);
    - ``rejected_ids``: distinct rejected ids in first appearance order.

    Any inconsistent id reuse raises ``ValueError`` from ``allocate``; caller
    inputs are never mutated, including on that error.
    """
    outcome = allocate(stock, orders)
    remaining = outcome['remaining']

    decisions = {}
    rejected_ids = []
    for row in outcome['results']:
        order_id = row['id']
        if order_id in decisions:
            # Equivalent replay: counted once.
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
        'rejected_unique': sum(1 for accepted in decisions.values() if not accepted),
        'units_reserved': sum(reserved_by_sku.values()),
        'reserved_by_sku': reserved_by_sku,
        'rejected_ids': rejected_ids,
    }
