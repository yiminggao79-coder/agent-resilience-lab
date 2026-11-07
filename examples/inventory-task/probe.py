"""Public development observation adapter; copied only into final judge copies."""
import copy
from allocation import allocate


def observe(stock, orders):
    before = copy.deepcopy((stock, orders))
    try:
        result = allocate(stock, orders)
    except ValueError:
        result = {'error': 'ValueError'}
    return {'output': result, 'inputs_unchanged': (stock, orders) == before}
