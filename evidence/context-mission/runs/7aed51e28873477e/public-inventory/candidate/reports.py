"""Read-only reporting built on top of the allocation engine.

This module deliberately contains no allocation logic of its own: every
decision comes from :func:`allocation.allocate`, so the rules for combining
lines, atomic rejection, idempotent order IDs and conflicting payloads are
exactly the ones enforced there.
"""

from allocation import allocate


def build_report(stock, orders):
    """Summarise an allocation run.

    Returns a dict with exactly these keys:

    ``remaining``
        Stock after allocation (all original keys, including zero counts).
    ``accepted_unique`` / ``rejected_unique``
        Number of distinct order IDs whose *first* decision was accepted /
        rejected. Equivalent replays are not counted again.
    ``units_reserved``
        Total units actually consumed across stock keys.
    ``reserved_by_sku``
        Every original stock key mapped to the units consumed for it, with
        zero entries included, inserted in sorted key order.
    ``rejected_ids``
        Distinct rejected IDs in order of first appearance.

    Conflicting payloads for one ID raise ValueError from the allocation
    engine. Caller inputs are copied up front and never mutated, including on
    that error.
    """
    # Copies keep the caller's objects pristine even if the engine raised or
    # a caller later mutated what it passed in.
    safe_stock = dict(stock)
    safe_orders = [
        {'id': order['id'],
         'items': [{'sku': item['sku'], 'qty': item['qty']}
                   for item in order['items']]}
        for order in orders
    ]

    outcome = allocate(safe_stock, safe_orders)
    remaining = outcome['remaining']

    accepted_unique = 0
    rejected_unique = 0
    rejected_ids = []
    decided = set()
    for row in outcome['results']:
        order_id = row['id']
        if order_id in decided:
            # Equivalent replay: already counted by its first decision.
            continue
        decided.add(order_id)
        if row['accepted']:
            accepted_unique += 1
        else:
            rejected_unique += 1
            rejected_ids.append(order_id)

    reserved_by_sku = {}
    for sku in sorted(remaining):
        consumed = safe_stock[sku] - remaining[sku]
        reserved_by_sku[sku] = consumed
    units_reserved = sum(reserved_by_sku[sku] for sku in reserved_by_sku)

    return {
        'remaining': remaining,
        'accepted_unique': accepted_unique,
        'rejected_unique': rejected_unique,
        'units_reserved': units_reserved,
        'reserved_by_sku': reserved_by_sku,
        'rejected_ids': rejected_ids,
    }
