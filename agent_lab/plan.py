"""Bounded model-authored checklist state; never execution authority."""
import copy
import json

from .tools import ToolError

MAX_STEPS = 32
MAX_STEP_BYTES = 1024
MAX_EXPLANATION_BYTES = 4096
MAX_PLAN_BYTES = 65536
STATUSES = ('pending', 'in_progress', 'completed')


def schema():
    return {'type': 'function', 'function': {
        'name': 'update_plan',
        'description': ('Replace the optional task checklist with step/status items and an optional explanation. '
                        'At most one step may be in_progress; an empty plan clears it. The checklist persists '
                        'across session turns. It never authorizes tools, executes steps, or verifies success. '
                        'Limits: 32 steps, 1024 UTF-8 bytes per nonblank step, 4096 explanation bytes, '
                        '65536 serialized JSON bytes.'),
        'parameters': {'type': 'object', 'properties': {
            'explanation': {'type': 'string', 'maxLength': MAX_EXPLANATION_BYTES},
            'plan': {'type': 'array', 'maxItems': MAX_STEPS, 'items': {
                'type': 'object', 'properties': {
                    'step': {'type': 'string', 'minLength': 1, 'maxLength': MAX_STEP_BYTES},
                    'status': {'type': 'string', 'enum': list(STATUSES)}},
                'required': ['step', 'status'], 'additionalProperties': False}}},
            'required': ['plan'], 'additionalProperties': False}}}


def validate_arguments(args):
    """Validate before intent persistence and again before recovered updates."""
    if (not isinstance(args, dict) or set(args) not in ({'plan'}, {'plan', 'explanation'})
            or not isinstance(args['plan'], list) or len(args['plan']) > MAX_STEPS):
        raise ToolError('invalid_arguments')
    try:
        if 'explanation' in args and (not isinstance(args['explanation'], str)
                or len(args['explanation'].encode('utf-8')) > MAX_EXPLANATION_BYTES):
            raise ToolError('invalid_arguments')
        in_progress = 0
        for item in args['plan']:
            if (not isinstance(item, dict) or set(item) != {'step', 'status'}
                    or not isinstance(item['step'], str) or not item['step'].strip()
                    or len(item['step'].encode('utf-8')) > MAX_STEP_BYTES
                    or not isinstance(item['status'], str) or item['status'] not in STATUSES):
                raise ToolError('invalid_arguments')
            in_progress += item['status'] == 'in_progress'
        if in_progress > 1 or len(json.dumps(args, ensure_ascii=False).encode('utf-8')) > MAX_PLAN_BYTES:
            raise ToolError('invalid_arguments')
    except UnicodeError:
        raise ToolError('invalid_arguments') from None
    return copy.deepcopy(args)


def replacement(state, args):
    args = validate_arguments(args)
    return {'revision': state.get('current_plan', {}).get('revision', 0) + 1,
            'turn_id': state.get('turn_id'), 'explanation': args.get('explanation'), 'plan': args['plan']}


def plan_summary(state):
    """Return metadata only; raw plan text belongs to explicit private views."""
    current = state.get('current_plan')
    if current is None:
        return None
    return {'revision': current['revision'], 'total': len(current['plan']),
            **{status: sum(item['status'] == status for item in current['plan']) for status in STATUSES}}
