from allocation import allocate


def build_report(stock, orders):
    """Summarize the outcome of allocating ``stock`` to ``orders``.

    Delegates every allocation decision to :func:`allocation.allocate`, so all
    existing rules (arrival order, combined SKU lines, atomic rejections,
    unknown SKUs, empty orders, idempotent replay by order id and ``ValueError``
    on conflicting payloads) are inherited rather than reimplemented.

    Returns a dict with:

    ``remaining``       stock after allocation, every original key preserved.
    ``accepted_unique`` number of distinct ids whose first decision accepted.
    ``rejected_unique`` number of distinct ids whose first decision rejected.
    ``units_reserved``  total units consumed across all stock keys.
    ``reserved_by_sku`` each original stock key mapped to the units consumed
                        from it (zeros included), keys in sorted order.
    ``rejected_ids``    distinct rejected ids in order of first appearance.

    Neither ``stock`` nor ``orders`` is mutated; ``allocate`` works on its own
    copy and raises ``ValueError`` before exposing any partial result.
    """
    result = allocate(stock, orders)
    remaining = result['remaining']

    # Distinct ids, keyed by first appearance; later rows are replays of the
    # first decision for that id (allocate guarantees consistent replays).
    first_decision = {}
    rejected_ids = []
    for row in result['results']:
        order_id = row['id']
        if order_id in first_decision:
            continue
        accepted = bool(row['accepted'])
        first_decision[order_id] = accepted
        if not accepted:
            rejected_ids.append(order_id)

    accepted_unique = sum(1 for accepted in first_decision.values() if accepted)
    rejected_unique = sum(1 for accepted in first_decision.values() if not accepted)

    reserved_by_sku = {}
    for sku in sorted(stock):
        reserved_by_sku[sku] = stock[sku] - remaining[sku]

    return {
        'remaining': remaining,
        'accepted_unique': accepted_unique,
        'rejected_unique': rejected_unique,
        'units_reserved': sum(reserved_by_sku.values()),
        'reserved_by_sku': reserved_by_sku,
        'rejected_ids': rejected_ids,
    }
