"""Authoring helper: separate direct/integrated outcomes, including errors."""
A_ACTIONS='''
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
'''
B_ACTIONS='''
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
'''
PAIR='''
    def _attempt(action):
        try: return {'ok':True,'value':action()}
        except (ValueError,KeyError,ImportError,AttributeError) as exc: return {'ok':False,'error':type(exc).__name__}
    def _pair(integrated,direct):
        left,right=_attempt(integrated),_attempt(direct)
        report=left['value'] if left['ok'] else {'error':left['error']}
        return {'report':report,'direct_consistent':left==right}
'''
