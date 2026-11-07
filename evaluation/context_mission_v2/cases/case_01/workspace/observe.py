"""Protected observation boundary; finite answers are external."""
import copy

def _observe(payload, action):
    before = copy.deepcopy(payload)
    try:
        value = action()
    except (ValueError, KeyError, ImportError, AttributeError) as exc:
        value = {'error': type(exc).__name__}
    return {'value': value, 'input_intact': payload == before}

def first(payload):
    def action():
        from portions import apportion
        from ledger import balances
        return {'shares': apportion(payload['total'], payload['weights']),
                'balances': balances(payload['entries'])}
    return _observe(payload, action)

def second(payload):
    def action():
        from reimburse import recommend
        return recommend(payload['entries'], payload.get('payments', []))
    return _observe(payload, action)

def third(payload):
    def action():
        from closeout import installments
        return installments(payload['entries'], payload.get('payments', []), payload.get('cap', 1000))
    return _observe(payload, action)
