from allocation import allocate


def build_report(stock, orders):
    """Summarize an allocation run over ``stock`` and ``orders``.

    Delegates all allocation and idempotency rules to :func:`allocation.allocate`
    and derives counts from its result, so this module holds no independent
    allocation engine.

    Returns exactly:

    ``remaining``
        Stock after allocation (as returned by ``allocate``).
    ``accepted_unique``
        Number of distinct order IDs whose first decision was accepted.
    ``rejected_unique``
        Number of distinct order IDs whose first decision was rejected.
    ``units_reserved``
        Total units consumed across all stock keys.
    ``reserved_by_sku``
        Each original stock key mapped to the units consumed, including zeros,
        inserted in sorted key order.
    ``rejected_ids``
        Distinct rejected IDs in first appearance order.

    Equivalent replays are counted once and consume nothing because
    ``allocate`` replays them without reserving stock. A reused ID carrying
    different combined quantities raises ``ValueError`` from ``allocate``; the
    caller's ``stock`` and ``orders`` are never mutated, even on that error.
    """
    result = allocate(stock, orders)
    remaining = result['remaining']

    accepted_ids = []
    rejected_ids = []
    for row in result['results']:
        bucket = accepted_ids if row['accepted'] else rejected_ids
        if row['id'] not in bucket:
            bucket.append(row['id'])

    reserved_by_sku = {}
    for sku in sorted(stock):
        reserved_by_sku[sku] = stock[sku] - remaining.get(sku, 0)

    return {
        'remaining': remaining,
        'accepted_unique': len(accepted_ids),
        'rejected_unique': len(rejected_ids),
        'units_reserved': sum(reserved_by_sku.values()),
        'reserved_by_sku': reserved_by_sku,
        'rejected_ids': rejected_ids,
    }
