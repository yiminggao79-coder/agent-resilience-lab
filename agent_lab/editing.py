"""Version-bound exact local edits without checkpointing reconstructed files."""
import re
import stat
import time

from .tools import MAX_BYTES, ToolError, UncertainEffect, digest

MAX_EDIT_FILE_BYTES = 2 * 1024 * 1024
MAX_EDITS = 16
MAX_EDIT_BYTES = MAX_BYTES


def schema():
    return {'type': 'function', 'function': {
        'name': 'edit_file',
        'description': ('Apply 1–16 ordered exact unique substring replacements to an existing visible UTF-8 file '
                        'up to 2 MiB. Get expected_sha256 from read_lines first; changed source versions conflict. '
                        'Each old must be nonempty and occur exactly once in the successively edited text; new may '
                        'be empty. Combined old+new UTF-8 bytes at most 64 KiB, every intermediate result at most '
                        '2 MiB. Ordinary file mode is preserved; no file creation, fuzzy matching or chmod override.'),
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string'}, 'expected_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
            'edits': {'type': 'array', 'minItems': 1, 'maxItems': MAX_EDITS, 'items': {
                'type': 'object', 'properties': {'old': {'type': 'string'}, 'new': {'type': 'string'}},
                'required': ['old', 'new'], 'additionalProperties': False}}},
            'required': ['path', 'expected_sha256', 'edits'], 'additionalProperties': False}}}


def _arguments(args):
    if (not isinstance(args, dict) or set(args) != {'path', 'expected_sha256', 'edits'}
            or not isinstance(args['path'], str) or not isinstance(args['expected_sha256'], str)
            or re.fullmatch('[0-9a-f]{64}', args['expected_sha256']) is None
            or not isinstance(args['edits'], list) or not 1 <= len(args['edits']) <= MAX_EDITS):
        raise ToolError('invalid_arguments')
    total, edits = 0, []
    try:
        for item in args['edits']:
            if (not isinstance(item, dict) or set(item) != {'old', 'new'}
                    or not isinstance(item['old'], str) or not item['old'] or not isinstance(item['new'], str)):
                raise ToolError('invalid_arguments')
            total += len(item['old'].encode('utf-8')) + len(item['new'].encode('utf-8'))
            if total > MAX_EDIT_BYTES:
                raise ToolError('input_too_large')
            edits.append({'old': item['old'], 'new': item['new']})
        args['path'].encode('utf-8')
    except UnicodeError:
        raise ToolError('invalid_data') from None
    return {'path': args['path'], 'expected_sha256': args['expected_sha256'], 'edits': edits}


def _read(tools, path):
    with tools._parent_directory(path) as (directory, filename):
        data, info = tools._read_at(directory, filename, MAX_EDIT_FILE_BYTES)
    return data, stat.S_IMODE(info.st_mode)


def _apply(data, edits):
    text = data.decode('utf-8')
    for item in edits:
        first = text.find(item['old'])
        if first < 0 or text.find(item['old'], first + 1) >= 0:
            raise ToolError('edit_conflict')
        text = text[:first] + item['new'] + text[first + len(item['old']):]
        encoded = text.encode('utf-8')
        if len(encoded) > MAX_EDIT_FILE_BYTES:
            raise ToolError('file_too_large')
    return encoded


def prepare_edit(tools, args):
    args = _arguments(args)
    tools._check_read_only({'name': 'edit_file', 'args': args})
    try:
        original, mode = _read(tools, args['path'])
        tools._ordinary_mode(mode)
        before = digest(original)
        if before != args['expected_sha256']:
            raise ToolError('file_version_mismatch')
        desired = _apply(original, args['edits'])
        return {'name': 'edit_file', 'args': args, 'policy': 'verify',
                'before': before, 'desired': digest(desired), 'before_mode': mode, 'desired_mode': mode}
    except UnicodeError:
        raise ToolError('invalid_data') from None
    except FileNotFoundError:
        raise ToolError('not_found') from None
    except OSError:
        raise ToolError('file_io_error') from None


def execute_edit(tools, intent, timeout, recovering=False):
    tools._check_read_only(intent)
    deadline = time.monotonic() + max(0, timeout)
    try:
        args = _arguments(intent['args'])
        if (intent.get('name') != 'edit_file' or intent.get('policy') != 'verify'
                or intent.get('before') != args['expected_sha256']
                or not isinstance(intent.get('desired'), str)
                or re.fullmatch('[0-9a-f]{64}', intent['desired']) is None
                or 'before_mode' not in intent or 'desired_mode' not in intent
                or intent['before_mode'] != intent['desired_mode']):
            raise UncertainEffect('invalid_edit_intent')
        tools._ordinary_mode(intent['before_mode'])
        original, mode = _read(tools, args['path'])
        completed, desired_mode = tools._write_boundary(intent, digest(original), mode)
        if completed:
            return {'ok': True, 'sha256': intent['desired'], 'mode': mode, 'reconciled': True}
        if time.monotonic() >= deadline:
            raise ToolError('tool_timeout')
        desired = _apply(original, args['edits'])
        if digest(desired) != intent['desired']:
            raise UncertainEffect('edit_result_mismatch')
        if time.monotonic() >= deadline:
            raise ToolError('tool_timeout')
        tools._atomic_write(tools.path(args['path']), desired, desired_mode)
        return {'ok': True, 'sha256': intent['desired'], 'mode': desired_mode, 'reconciled': False}
    except (UnicodeError, OSError, ToolError):
        raise UncertainEffect('edit_target_unverifiable') from None
