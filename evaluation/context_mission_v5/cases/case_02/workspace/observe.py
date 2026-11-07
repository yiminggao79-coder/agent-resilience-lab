import copy
def _mutable_ids(value,seen=None):
    if seen is None: seen=set()
    if not isinstance(value,(dict,list)): return set()
    identity=id(value)
    if identity in seen: return set()
    seen.add(identity)
    result={identity}
    children=value.values() if isinstance(value,dict) else value
    for child in children: result.update(_mutable_ids(child,seen))
    return result
def _observe(payload,action):
    before=copy.deepcopy(payload)
    try: result=action()
    except (ValueError,KeyError,ImportError,AttributeError) as exc: result={'error':type(exc).__name__}
    return {'result':result,'preserved':payload==before,
            'detached':not bool(_mutable_ids(result)&_mutable_ids(payload))}

def _attempt(action):
    try: return {'ok':True,'value':action()}
    except (ValueError,KeyError,ImportError,AttributeError) as exc: return {'ok':False,'error':type(exc).__name__}
def _pair(integrated,direct):
    left,right=_attempt(integrated),_attempt(direct)
    report=left['value'] if left['ok'] else {'error':left['error']}
    return {'report':report,'direct_consistent':left==right}

def first(payload):
    def action():
        from api import normalize
        from score import canonical,measure
        def direct():
            notes=canonical(payload['events'])
            return {'events':notes,'metrics':measure(notes)}
        return _pair(lambda:normalize(payload['events']),direct)
    return _observe(payload,action)
def second(payload):
    def action():
        from api import transform_report
        from transforms import apply
        from score import measure
        options=payload.get('options',{})
        def direct():
            notes=apply(payload['events'],**options)
            return {'events':notes,'metrics':measure(notes)}
        return _pair(lambda:transform_report(payload['events'],**options),direct)
    return _observe(payload,action)
def third(payload):
    def action():
        from api import arrange
        from arrangement import build
        return _pair(lambda:arrange(payload['tracks']),lambda:build(payload['tracks']))
    return _observe(payload,action)
def fourth(payload):
    def action():
        from api import render
        from sequencer import compile
        return _pair(lambda:render(payload['tracks'],payload['tempo'],payload.get('ticks_per_beat',480)),
            lambda:compile(payload['tracks'],payload['tempo'],payload.get('ticks_per_beat',480)))
    return _observe(payload,action)
