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
        from api import combine
        from joins import left_join
        from grouping import summarize
        args=[payload[key] for key in ('left','right','left_keys','right_keys','group_keys','value')]
        prefix=payload.get('prefix','r_')
        def direct():
            table=left_join(payload['left'],payload['right'],payload['left_keys'],payload['right_keys'],prefix)
            return {'joined':table,'groups':summarize(table,payload['group_keys'],payload['value'])}
        return _pair(lambda:combine(*args,prefix),direct)
    return _observe(payload,action)
def second(payload):
    def action():
        from api import analyze
        from analytics import aggregate
        from ordering import sort_table
        return _pair(lambda:analyze(payload['table'],payload['keys'],payload['measures'],payload.get('order',[])),
            lambda:sort_table(aggregate(payload['table'],payload['keys'],payload['measures']),payload.get('order',[])))
    return _observe(payload,action)
def third(payload):
    def action():
        from api import enrich
        from windows import compute
        from ordering import sort_table
        def direct():
            table=compute(payload['table'],payload['partition_by'],payload['order_by'],payload['calculations'])
            return {'table':table,'ordered':sort_table(table,payload.get('output_order',[]))}
        return _pair(lambda:enrich(payload['table'],payload['partition_by'],payload['order_by'],payload['calculations'],payload.get('output_order',[])),direct)
    return _observe(payload,action)
def fourth(payload):
    def action():
        from api import pipeline
        from query import execute
        return _pair(lambda:pipeline(payload['sources'],payload['steps'],payload['outputs']),
            lambda:execute(payload['sources'],payload['steps'],payload['outputs']))
    return _observe(payload,action)
