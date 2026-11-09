"""Summary reports derived from the allocation engine in :mod:`allocation`."""

from allocation import allocate


def build_report(stock, orders):
    """Summarize the outcome of allocating ``orders`` against ``stock``.

    Delegates all allocation to :func:`allocation.allocate`, so every
    allocation rule (atomic rejection, combined lines, case-sensitive SKUs,
    idempotent IDs, ``ValueError`` on conflicting ID payloads) is inherited
    rather than reimplemented. Caller inputs are never mutated.

    Returns exactly:

    * ``remaining`` -- stock after allocation.
    * ``accepted_unique`` -- number of distinct IDs whose first decision was accepted.
    * ``rejected_unique`` -- number of distinct IDs whose first decision was rejected.
    * ``units_reserved`` -- total units consumed across all stock keys.
    * ``reserved_by_sku`` -- every original stock key mapped to the units
      consumed from it (zeros included), inserted in sorted key order.
    * ``rejected_ids`` -- distinct rejected IDs in first-appearance order.
    """
    allocation = allocate(stock, orders)
    remaining = allocation['remaining']

    # Only the first row per ID carries the decision; replays mirror it and
    # consume nothing, so they must not be counted twice.
    decision = {}
    for row in allocation['results']:
        if row['id'] not in decision:
            decision[row['id']] = row['accepted']

    rejected_ids = [order_id for order_id, accepted in decision.items() if not accepted]

    reserved_by_sku = {}
    for sku in sorted(stock):
        reserved_by_sku[sku] = stock[sku] - remaining[sku]

    return {
        'remaining': remaining,
        'accepted_unique': sum(1 for accepted in decision.values() if accepted),
        'rejected_unique': len(rejected_ids),
        'units_reserved': sum(reserved_by_sku.values()),
        'reserved_by_sku': reserved_by_sku,
        'rejected_ids': rejected_ids,
    }
