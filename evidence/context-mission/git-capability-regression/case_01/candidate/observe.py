"""Protected observations; finite answers exist only outside the workspace."""
import copy
def _observe(payload,action):
    before=copy.deepcopy(payload)
    try: result=action()
    except (ValueError,KeyError,ImportError,AttributeError) as exc: result={'error':type(exc).__name__}
    return {'result':result,'preserved':payload==before}
def first(payload):
    def action():
        from api import calculate
        return calculate(payload['expression'],payload.get('values',{}))
    return _observe(payload,action)
def second(payload):
    def action():
        from api import slope
        report=slope(payload['expression'],payload.get('values',{}),payload['variable'])
        from differentiation import derivative
        from parsing import parse
        from evaluation import evaluate,pair
        direct=pair(evaluate(derivative(parse(payload['expression']),payload['variable']),payload.get('values',{})))
        return {'report':report,'direct_consistent':report['slope']==direct}
    return _observe(payload,action)
def third(payload):
    def action():
        from api import reduced
        report=reduced(payload['expression'],payload.get('values',{}),payload['variable'])
        from simplification import reduce
        from parsing import parse
        from rendering import render
        return {'report':report,'direct_consistent':report['canonical']==render(reduce(parse(payload['expression'])))}
    return _observe(payload,action)
def fourth(payload):
    def action():
        from api import workbook_report
        report=workbook_report(payload['formulas'],payload['inputs'],payload['outputs'],payload['wrt'])
        from workbook import run
        direct=run(payload['formulas'],payload['inputs'],payload['outputs'],payload['wrt'])
        return {'report':report,'direct_consistent':all(report[key]==direct[key] for key in ('order','results'))}
    return _observe(payload,action)
