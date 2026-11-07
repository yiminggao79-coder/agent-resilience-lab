"""Finite public development observations, kept outside candidate workspace."""
import copy


def observe(stock, batches):
    from allocation import Inventory
    before = copy.deepcopy((stock, batches))
    inventory = Inventory(stock)
    rows = []
    detached = True
    for batch in batches:
        try:
            result = inventory.allocate(batch)
            public_result = copy.deepcopy(result)
        except ValueError:
            result = None
            public_result = {'error': 'ValueError'}
        snapshot = copy.deepcopy(inventory.snapshot())
        exposed = inventory.snapshot()
        exposed['__observation_only__'] = 123456
        detached = detached and inventory.snapshot() == snapshot
        if result is not None:
            result['remaining']['__observation_only__'] = 654321
            detached = detached and inventory.snapshot() == snapshot
        rows.append({'result': public_result, 'snapshot': snapshot})
    return {'batches': rows, 'detached': detached, 'inputs_unchanged': (stock, batches) == before}
