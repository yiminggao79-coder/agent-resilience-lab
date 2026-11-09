"""Allocation reporting built on top of :func:`allocation.allocate`.

This module deliberately contains no allocation logic of its own: every
acceptance decision and stock movement comes from ``allocate`` so the two
APIs can never drift apart.
"""
from allocation import allocate


def build_report(stock, orders):
    """Summarize the effect of allocating ``orders`` against ``stock``.

    Returns a dict with exactly the keys ``remaining``, ``accepted_unique``,
    ``rejected_unique``, ``units_reserved``, ``reserved_by_sku`` and
    ``rejected_ids``.

    Distinct order IDs are counted by their first decision only, so
    equivalent replays are counted once and consume nothing. Conflicting
    payloads for one ID propagate ``ValueError`` from ``allocate``. Neither
    argument is mutated, including on that error.
    """
    # allocate works on its own copy of stock and never mutates orders, so the
    # caller's inputs stay untouched -- including when it raises ValueError.
    outcome = allocate(stock, orders)
    remaining = outcome['remaining']

    accepted_ids = []
    rejected_ids = []
    for row in outcome['results']:
        order_id = row['id']
        if row['accepted']:
            accepted_ids.append(order_id)
        else:
            rejected_ids.append(order_id)

    # Distinct IDs in first-appearance order; each ID's decision is fixed by
    # its first occurrence, so duplicates collapse to that single decision.
    accepted_unique = len(dict.fromkeys(accepted_ids))
    rejected_order = list(dict.fromkeys(rejected_ids))

    reserved_by_sku = {}
    total_reserved = 0
    for sku in sorted(stock):
        consumed = stock[sku] - remaining[sku]
        reserved_by_sku[sku] = consumed
        total_reserved += consumed

    return {
        'remaining': remaining,
        'accepted_unique': accepted_unique,
        'rejected_unique': len(rejected_order),
        'units_reserved': total_reserved,
        'reserved_by_sku': reserved_by_sku,
        'rejected_ids': rejected_order,
    }
