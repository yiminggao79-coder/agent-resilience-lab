"""Protected JSON boundary. Expected results live outside this workspace."""
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
        from schedule import conflicts
        from reporting import day_report
        return {'conflicts': conflicts(payload['rows']),
                'report': day_report(payload['rows'], payload['day'])}
    return _call(payload, run)

def stage2(payload):
    def run():
        from requests import allocate
        return allocate(payload['existing'], payload['requests'], payload['capacities'], payload.get('blocks', []))
    return _call(payload, run)

def stage3(payload):
    def run():
        from changes import apply_groups
        return apply_groups(payload['existing'], payload['groups'], payload['capacities'], payload.get('blocks', []))
    return _call(payload, run)
