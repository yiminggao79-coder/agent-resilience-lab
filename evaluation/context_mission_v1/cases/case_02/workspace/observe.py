"""Protected observation adapter. It contains no oracle answers."""
import copy

def _call(payload, operation):
    before = copy.deepcopy(payload)
    try:
        result = operation()
    except (ValueError, KeyError, ImportError, AttributeError) as exc:
        result = {'error': type(exc).__name__}
    return {'result': result, 'preserved': payload == before}

def stage1(payload):
    def run():
        from searching import search
        from reporting import describe
        return {'hits': search(payload['documents'], payload['query']), 'metrics': describe(payload['documents'])}
    return _call(payload, run)

def stage2(payload):
    def run():
        from phrases import phrase_search
        return phrase_search(payload['documents'], payload['phrase'], payload.get('slop', 0))
    return _call(payload, run)

def stage3(payload):
    def run():
        from updates import revise_and_search
        return revise_and_search(payload['documents'], payload['edits'], payload['query'],
                                 payload.get('phrase'), payload.get('slop', 0))
    return _call(payload, run)
