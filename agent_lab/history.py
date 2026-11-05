"""Pure bounded projections of the private, append-only transcript archive."""
import hashlib
import json
import re

from .tools import ToolError

MAX_FRAGMENT_BYTES = 8192


def _projection(message):
    if not isinstance(message, dict) or message.get('role') not in ('system', 'developer', 'user', 'assistant', 'tool'):
        raise ToolError('invalid_data')
    projected = {'role': message['role']}
    for field in ('content', 'tool_call_id'):
        if field in message:
            projected[field] = message[field]
    if '_skill_script_snapshot' in message:
        snapshot=message['_skill_script_snapshot']
        fields=('purpose','path','sha256','skill_sha256','content')
        if (message['role']!='tool' or type(snapshot) is not dict or any(type(snapshot.get(k)) is not str for k in fields)
                or snapshot['purpose']!='prepared_command_source' or len(snapshot['path'])>1024
                or any(re.fullmatch('[a-f0-9]{64}',snapshot[k]) is None for k in ('sha256','skill_sha256'))):
            raise ToolError('invalid_data')
        try:raw=snapshot['content'].encode('utf-8')
        except UnicodeError:raise ToolError('invalid_data') from None
        if len(raw)>16384 or hashlib.sha256(raw).hexdigest()!=snapshot['sha256']:raise ToolError('invalid_data')
        # Explicit archive access/summary input may recover the supplied source;
        # actual runtime policy/command internals stay private and unprojected.
        projected['skill_script_snapshot']={key:snapshot[key] for key in fields}
    if 'tool_calls' in message:
        if not isinstance(message['tool_calls'], list):
            raise ToolError('invalid_data')
        calls = []
        for call in message['tool_calls']:
            if not isinstance(call, dict) or not isinstance(call.get('function'), dict):
                raise ToolError('invalid_data')
            function = call['function']
            calls.append({**{key: call[key] for key in ('id', 'type') if key in call},
                          'function': {key: function[key] for key in ('name', 'arguments') if key in function}})
        projected['tool_calls'] = calls
    return projected


def lookup_message(messages, index, offset=0, limit=4096):
    """Page canonical ASCII JSON; offsets are serialized bytes, not text chars.

    Callers bound the supplied archive to messages preceding the current lookup
    operation. This helper neither reads storage nor chooses a live task.
    """
    if (not isinstance(messages, list) or type(index) is not int
            or not 0 <= index < len(messages) or type(offset) is not int or offset < 0
            or type(limit) is not int or not 1 <= limit <= MAX_FRAGMENT_BYTES):
        raise ToolError('invalid_arguments')
    try:
        serialized = json.dumps(_projection(messages[index]), sort_keys=True,
                                separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ToolError('invalid_data') from None
    if offset > len(serialized):
        raise ToolError('invalid_arguments')
    end = min(len(serialized), offset + limit)
    return {'ok': True, 'index': index, 'offset': offset,
            'total_bytes': len(serialized),
            'projection_sha256': hashlib.sha256(serialized.encode('ascii')).hexdigest(),
            'encoding': 'ascii_json', 'fragment': serialized[offset:end],
            'next_offset': end if end < len(serialized) else None,
            'complete': end == len(serialized)}
