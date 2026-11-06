"""Independent bounded patch grammar; compile to existing verified file intents.

The runtime checkpoints each step. This module promises no multi-file atomicity.
"""
import json
import os
import unicodedata

from .tools import MAX_BYTES, ToolError, UncertainEffect, digest

MAX_PATCH_BYTES = 65536
MAX_PATCH_PATHS = 16
MAX_PATCH_TRANSPORT_BYTES = 2 * 1024 * 1024
MAX_PATCH_CHUNKS = 256


def schema():
    return {'type': 'function', 'function': {
        'name': 'apply_patch_text',
        'description': ('Apply bounded Codex-style *** Begin Patch / *** End Patch text: Add File, Delete File, '
                        'Update File with explicit @@ or @@ exact-anchor chunks, optional Move to and End of File. '
                        'Exact unique LF line contexts only; no fuzzy matching. Add/move destinations must be absent, '
                        'parents must exist. Visible relative paths, at most 16 affected paths, patch and each file '
                        'at most 64 KiB. Multiple files commit sequentially; interruption/failure can leave a partial patch.'),
        'parameters': {'type': 'object', 'properties': {'patch': {'type': 'string'}},
                       'required': ['patch'], 'additionalProperties': False}}}


def _parse(text):
    lines = text.split('\n')
    if lines[-1:] == ['']:
        lines.pop()
    if len(lines) < 3 or lines[0] != '*** Begin Patch' or lines[-1] != '*** End Patch':
        raise ToolError('invalid_patch')
    sections, index, chunks = [], 1, 0
    while index < len(lines) - 1:
        header = lines[index]
        kind = next((kind for kind in ('Add', 'Update', 'Delete')
                     if header.startswith('*** ' + kind + ' File: ')), None)
        if kind is None:
            raise ToolError('invalid_patch')
        section = {'kind': kind, 'path': header[len('*** ' + kind + ' File: '):], 'chunks': []}
        sections.append(section)
        if len(sections) > MAX_PATCH_PATHS:
            raise ToolError('patch_too_many_files')
        index += 1
        if kind == 'Update' and lines[index].startswith('*** Move to: '):
            section['move'] = lines[index][len('*** Move to: '):]
            index += 1
        if kind == 'Add':
            added = []
            while index < len(lines) - 1 and lines[index].startswith('+'):
                added.append(lines[index][1:] + '\n')
                index += 1
            section['content'] = ''.join(added)
        elif kind == 'Update':
            while index < len(lines) - 1 and (lines[index] == '@@' or lines[index].startswith('@@ ')):
                chunk = {'anchor': lines[index][3:] if lines[index].startswith('@@ ') else None,
                         'lines': [], 'eof': False}
                if chunk['anchor'] == '':
                    raise ToolError('invalid_patch')
                section['chunks'].append(chunk)
                chunks += 1
                if chunks > MAX_PATCH_CHUNKS:
                    raise ToolError('patch_too_many_chunks')
                index += 1
                while index < len(lines) - 1 and lines[index][:1] in (' ', '+', '-'):
                    chunk['lines'].append(lines[index])
                    index += 1
                if not chunk['lines']:
                    raise ToolError('invalid_patch')
                if lines[index] == '*** End of File':
                    chunk['eof'] = True
                    index += 1
            if not section['chunks']:
                raise ToolError('invalid_patch')
    return sections


def _unique(lines, pattern, start, eof=False):
    candidates = [index for index in range(start, len(lines) - len(pattern) + 1)
                  if (not eof or index + len(pattern) == len(lines))
                  and lines[index:index + len(pattern)] == pattern]
    if len(candidates) != 1:
        raise ToolError('patch_conflict')
    return candidates[0]


def _updated(content, chunks):
    if '\r' in content:
        raise ToolError('patch_line_endings_unsupported')
    terminated = content.endswith('\n')
    lines = content.split('\n') if content else []
    if terminated:
        lines.pop()
    cursor, output = 0, []
    for chunk in chunks:
        start = cursor
        if chunk['anchor'] is not None:
            start = _unique(lines, [chunk['anchor']], cursor) + 1
        old = [line[1:] for line in chunk['lines'] if line[0] in (' ', '-')]
        new = [line[1:] for line in chunk['lines'] if line[0] in (' ', '+')]
        if old:
            at = _unique(lines, old, start, chunk['eof'])
        else:
            at = start if chunk['anchor'] is not None else len(lines)
            if chunk['eof'] and at != len(lines):
                raise ToolError('patch_conflict')
        output.extend(lines[cursor:at])
        output.extend(new)
        cursor = at + len(old)
    output.extend(lines[cursor:])
    return '\n'.join(output) + ('\n' if output and terminated else '')


def _key(path):
    # Conservative on case-sensitive hosts; avoids macOS name aliases deleting
    # their own freshly written move destination.
    return unicodedata.normalize('NFD', path).casefold()


def prepare(tools, args):
    if not isinstance(args, dict) or set(args) != {'patch'} or not isinstance(args['patch'], str):
        raise ToolError('invalid_arguments')
    try:
        if len(args['patch'].encode('utf-8')) > MAX_PATCH_BYTES:
            raise ToolError('input_too_large')
        sections = _parse(args['patch'])
        paths, keys, existing = [], set(), []
        for section in sections:
            for value in [section['path']] + ([section['move']] if 'move' in section else []):
                path = tools.path(value)
                tools._check_read_only({'name': 'write_file', 'args': {'path': value}})
                key = _key(value)
                if key in keys or any(path.exists() and os.path.samefile(path, other) for other in existing):
                    raise ToolError('patch_path_conflict')
                if path.exists():
                    existing.append(path)
                paths.append(value)
                keys.add(key)
        if len(paths) > MAX_PATCH_PATHS:
            raise ToolError('patch_too_many_files')
        steps, sources, source_modes = [], {}, {}
        for section in sections:
            value, kind = section['path'], section['kind']
            path = tools.path(value)
            if not path.parent.is_dir():
                raise ToolError('parent_not_found')
            if path.is_dir():
                raise ToolError('not_a_file')
            original, mode = tools._snapshot(path, missing_ok=True)
            if mode is not None:
                tools._ordinary_mode(mode)
            before = digest(original) if original is not None else None
            sources[value], source_modes[value] = before, mode
            if kind == 'Add':
                if before is not None:
                    raise ToolError('patch_destination_exists')
                content = section['content']
            else:
                if before is None:
                    raise ToolError('not_found')
                # Content and permissions derive from the same stable FD read.
                if kind == 'Delete':
                    steps.append(_step({'name': 'delete_file', 'args': {'path': value},
                                        'policy': 'verify_delete', 'before': before, 'before_mode': mode}, 'deleted'))
                    continue
                content = _updated(original.decode('utf-8'), section['chunks'])
            encoded = content.encode('utf-8')
            if len(encoded) > MAX_BYTES:
                raise ToolError('file_too_large')
            destination = section.get('move', value)
            if 'move' in section:
                target = tools.path(destination)
                if not target.parent.is_dir():
                    raise ToolError('parent_not_found')
                if target.exists():
                    raise ToolError('patch_destination_exists')
                sources[destination] = None
                source_modes[destination] = None
            write = {'name': 'write_file', 'args': {'path': destination, 'content': content},
                     'policy': 'verify', 'before': None if 'move' in section else before,
                     'desired': digest(encoded), 'before_mode': None if 'move' in section else mode,
                     'desired_mode': 0o600 if kind == 'Add' else mode}
            steps.append(_step(write, 'move_destination' if 'move' in section else 'added' if kind == 'Add' else 'updated'))
            if 'move' in section:
                steps.append(_step({'name': 'delete_file', 'args': {'path': value},
                                    'policy': 'verify_delete', 'before': before, 'before_mode': mode}, 'move_source',
                                   guards={destination: write['desired']},
                                   guard_modes={destination: write['desired_mode']}))
        steps[0]['initial_sources'] = sources
        steps[0]['initial_modes'] = source_modes
        intent = {'name': 'apply_patch_text', 'args': {}, 'policy': 'verify_patch', 'steps': steps}
        if len(json.dumps(intent, ensure_ascii=False).encode('utf-8')) > MAX_PATCH_TRANSPORT_BYTES:
            raise ToolError('patch_intent_too_large')
        return intent
    except UnicodeError:
        raise ToolError('invalid_data') from None
    except OSError:
        raise ToolError('file_io_error') from None


def _step(file_intent, action, guards=None, guard_modes=None):
    return {'name': 'patch_step', 'args': {}, 'policy': 'verify_patch_step',
            'file_intent': file_intent, 'action': action, 'guards': guards or {},
            'guard_modes': guard_modes or {}}


def progress(pending):
    """Reject inconsistent durable progress before selecting a file effect."""
    steps = pending['intent'].get('steps')
    cursor = pending.setdefault('patch_cursor', 0)
    receipts = pending.setdefault('patch_receipts', [])
    inflight = pending.get('patch_inflight', False)
    if (not isinstance(steps, list) or not 1 <= len(steps) <= MAX_PATCH_PATHS
            or type(cursor) is not int or not 0 <= cursor <= len(steps)
            or not isinstance(receipts, list) or len(receipts) != cursor
            or type(inflight) is not bool or (cursor == len(steps) and inflight)):
        raise UncertainEffect('invalid_patch_progress')
    for index, step in enumerate(steps):
        if (not isinstance(step, dict) or step.get('name') != 'patch_step'
                or not isinstance(step.get('file_intent'), dict)
                or step['file_intent'].get('name') not in ('write_file', 'delete_file')
                or not isinstance(step['file_intent'].get('args'), dict)
                or not isinstance(step['file_intent']['args'].get('path'), str)):
            raise UncertainEffect('invalid_patch_progress')
        if index < cursor and (not isinstance(receipts[index], dict)
                               or receipts[index].get('path') != step['file_intent']['args']['path']
                               or receipts[index].get('ok') is not True):
            raise UncertainEffect('invalid_patch_progress')
    return steps


def execute_step(tools, intent, timeout, recovering=False):
    inner = intent['file_intent']
    tools._check_read_only(inner)
    try:
        current, mode = tools._file_state(tools.path(inner['args']['path']))
        if inner['name'] == 'write_file':
            completed, _ = tools._write_boundary(inner, current, mode)
        else:
            completed = current is None
            if not completed and (current != inner['before']
                                  or ('before_mode' in inner and mode != inner['before_mode'])):
                raise UncertainEffect('target_changed')
        for path, expected in intent.get('guards', {}).items():
            observed, observed_mode = tools._file_state(tools.path(path))
            if (observed != expected or (path in intent.get('guard_modes', {})
                                        and observed_mode != intent['guard_modes'][path])):
                raise UncertainEffect('patch_guard_changed')
        if not recovering or not completed:
            for path, expected in intent.get('initial_sources', {}).items():
                observed, observed_mode = tools._file_state(tools.path(path))
                if (observed != expected or (path in intent.get('initial_modes', {})
                                            and observed_mode != intent['initial_modes'][path])):
                    raise UncertainEffect('patch_source_changed')
        if recovering and not completed and current == inner['before'] and timeout <= 0:
            return {'ok': False, 'error': 'patch_deadline_no_effect'}
        # Dispatch directly inside the already leased trusted worker. Existing
        # write/delete CAS, atomic replace, fsync and reconciliation stay shared.
        operation = tools._recover if recovering else tools._execute
        return operation(inner, timeout)
    except ToolError as error:
        if error.code == 'tool_timeout':
            raise
        raise UncertainEffect('patch_target_unverifiable') from None
