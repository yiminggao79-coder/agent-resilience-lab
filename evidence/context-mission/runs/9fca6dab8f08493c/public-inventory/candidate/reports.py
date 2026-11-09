"""Reporting layer built on top of the single allocation engine."""
from allocation import allocate
from lines import identity, quantities


def build_report(stock, orders):
    """Summarise an allocation run without mutating caller inputs.

    Delegates all allocation to :func:`allocation.allocate`, so rejection
    atomicity, duplicate-line combining, idempotent replay and conflicting
    payload detection behave exactly as they do there. ``ValueError`` from
    conflicting order IDs propagates untouched and leaves inputs unchanged.
    """
    # Copy before allocating so the report is computed from pristine inputs
    # even if a later step raised; allocate() itself never mutates them.
    original = dict(stock)
    outcome = allocate(original, orders)

    decisions = {}       # order id -> first decision
    payloads = {}        # order id -> first identity (defensive consistency)
    rejected_ids = []
    for order, row in zip(orders, outcome['results']):
        order_id = row['id']
        if order_id in decisions:
            continue
        decisions[order_id] = row['accepted']
        payloads[order_id] = identity(quantities(order['items']))
        if not row['accepted']:
            rejected_ids.append(order_id)

    accepted_unique = sum(1 for ok in decisions.values() if ok)
    rejected_unique = sum(1 for ok in decisions.values() if not ok)

    reserved_by_sku = {}
    units_reserved = 0
    remaining = outcome['remaining']
    for sku in sorted(remaining):
        used = original[sku] - remaining[sku]
        reserved_by_sku[sku] = used
        units_reserved += used

    return {
        'remaining': remaining,
        'accepted_unique': accepted_unique,
        'rejected_unique': rejected_unique,
        'units_reserved': units_reserved,
        'reserved_by_sku': reserved_by_sku,
        'rejected_ids': rejected_ids,
    }
