from allocation import allocate


def build_report(stock, orders):
    """Summarise an allocation run.

    Delegates every allocation decision, replay and conflict check to
    :func:`allocation.allocate`; this module only derives summary figures from
    that result, so it never offers a second allocation engine.

    Returns exactly:

    * ``remaining`` -- stock after allocation (all original keys kept);
    * ``accepted_unique`` / ``rejected_unique`` -- number of distinct order IDs
      whose *first* decision was accepted / rejected;
    * ``units_reserved`` -- total units actually consumed across stock keys;
    * ``reserved_by_sku`` -- each original stock key mapped to units consumed,
      zeros included, inserted in sorted key order;
    * ``rejected_ids`` -- distinct rejected IDs in first-appearance order.

    Equivalent replays are counted once and consume nothing. A reused ID whose
    combined quantities differ raises ``ValueError`` (from ``allocate``).
    Caller inputs are never mutated, including on that error.
    """
    allocation = allocate(stock, orders)

    decisions = {}
    rejected_ids = []
    for row in allocation['results']:
        order_id = row['id']
        if order_id in decisions:
            continue
        decisions[order_id] = row['accepted']
        if not row['accepted']:
            rejected_ids.append(order_id)

    reserved_by_sku = {}
    for sku in sorted(stock):
        reserved_by_sku[sku] = stock[sku] - allocation['remaining'][sku]

    return {
        'remaining': allocation['remaining'],
        'accepted_unique': sum(1 for accepted in decisions.values() if accepted),
        'rejected_unique': sum(1 for accepted in decisions.values() if not accepted),
        'units_reserved': sum(reserved_by_sku.values()),
        'reserved_by_sku': reserved_by_sku,
        'rejected_ids': rejected_ids,
    }
