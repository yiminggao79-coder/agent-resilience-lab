"""Protected JSON observations. No external expected answers are included."""
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
        from runs import pack, unpack
        from metrics import histogram
        packed = pack(payload['grid'])
        return {'packed': packed, 'decoded': unpack(payload.get('packet', packed)),
                'histogram': histogram(payload['grid'])}
    return _observe(payload, action)

def second(payload):
    def action():
        from regions import paint, components
        grid = payload['grid']
        diagonal = payload.get('diagonal', False)
        return {'paint': paint(grid, payload['start'], payload['replacement'], diagonal),
                'components': components(grid, diagonal)}
    return _observe(payload, action)

def third(payload):
    def action():
        from layers import render
        return render(payload['base'], payload['layers'], payload.get('diagonal', False))
    return _observe(payload, action)
