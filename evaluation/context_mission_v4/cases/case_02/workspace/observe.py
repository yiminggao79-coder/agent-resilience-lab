"""Protected adapter with no external expected values."""
import copy
def _observe(payload,action):
    before=copy.deepcopy(payload)
    try: result=action()
    except (ValueError,KeyError,ImportError,AttributeError) as exc: result={'error':type(exc).__name__}
    return {'result':result,'preserved':payload==before}
def first(payload):
    def action():
        from manifest import make
        return make(payload['catalog'],payload['lines'])
    return _observe(payload,action)
def second(payload):
    def action():
        from manifest import packed
        report=packed(payload['catalog'],payload['lines'],payload['boxes'])
        from packing import pack
        return {'report':report,'direct_consistent':report['cartons']==pack(payload['catalog'],payload['lines'],payload['boxes'])}
    return _observe(payload,action)
def third(payload):
    def action():
        from manifest import constrained
        report=constrained(payload['catalog'],payload['lines'],payload['boxes'],payload.get('policy'))
        from packing import pack
        return {'report':report,'direct_consistent':report['cartons']==pack(payload['catalog'],payload['lines'],payload['boxes'],payload.get('policy'))}
    return _observe(payload,action)
def fourth(payload):
    def action():
        from manifest import dispatch_report
        report=dispatch_report(payload['catalog'],payload['stock'],payload['requests'],payload['boxes'],payload.get('policy'))
        from dispatch import allocate
        direct=allocate(payload['catalog'],payload['stock'],payload['requests'],payload['boxes'],payload.get('policy'))
        return {'report':report,'direct_consistent':report==direct}
    return _observe(payload,action)
