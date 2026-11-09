"""Protected observation adapter; answers remain external."""
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
        from presentation import season
        return season(payload['matches'], payload.get('teams', []))
    return _observe(payload, action)

def second(payload):
    def action():
        from discipline import review
        return review(payload['matches'], payload['actions'], payload.get('teams', []))
    return _observe(payload, action)

def third(payload):
    def action():
        from qualification import select
        return select(payload['matches'], payload['actions'], payload['groups'],
                      payload.get('slots', 1), payload.get('wildcards', 0))
    return _observe(payload, action)
