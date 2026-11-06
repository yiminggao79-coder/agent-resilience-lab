"""Stateless bounded literal search over the shared visible-file inventory.

Cursors bind observed bytes and metadata; they are not filesystem snapshots.
"""
import base64
import binascii
import hashlib
import json
import math
import re
import time

from .discovery import _path_parts, _scan
from .tools import MAX_BYTES, ToolError

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SEARCH_BYTES = 8 * 1024 * 1024
MAX_QUERY_BYTES = 1024
MAX_CURSOR_BYTES = 1024
MAX_PREVIEW_CHARS = 512
MAX_SKIP_EXAMPLES = 20
_LINE_BREAKS = '\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029'


def schema():
    return {'type': 'function', 'function': {
        'name': 'search_files_page',
        'description': ('Page literal case-sensitive UTF-8 content search over visible regular single-link files. '
                        'query must be nonempty single-line text (max 1024 UTF-8 bytes); optional directory prefix, '
                        'limit 1–200 (default 100), cursor from the previous page with identical arguments. '
                        'Results use Python splitlines text lines (same as read_lines), ordered path/line with 1-based Unicode column. '
                        'text is a max 512-character preview, not necessarily the whole line. Binary/oversized '
                        'files are explicitly skipped; no matches with skips does not prove absence. '
                        '2 MiB/file, 8 MiB read/scan; changed observed content/tree rejects the cursor. '
                        'No regex, hidden paths, links, subprocesses or persisted index.'),
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string'}, 'prefix': {'type': 'string'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200},
            'cursor': {'type': 'string', 'maxLength': MAX_CURSOR_BYTES}},
            'required': ['query'], 'additionalProperties': False}}}


def _encode_cursor(value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')
    return 's1.' + base64.urlsafe_b64encode(data).decode('ascii')


def _decode_cursor(value):
    try:
        if not isinstance(value, str) or len(value) > MAX_CURSOR_BYTES or not value.startswith('s1.'):
            raise ValueError()
        decoded = json.loads(base64.b64decode(value[3:], altchars=b'-_', validate=True))
        if (not isinstance(decoded, dict) or set(decoded) != {'q', 't', 'o'}
                or type(decoded['o']) is not int or not 1 <= decoded['o'] <= MAX_SEARCH_BYTES
                or any(not isinstance(decoded[key], str) or not re.fullmatch('[0-9a-f]{64}', decoded[key]) for key in ('q', 't'))
                or _encode_cursor(decoded) != value):
            raise ValueError()
        return decoded
    except (ValueError, UnicodeError, binascii.Error, RecursionError):
        raise ToolError('invalid_cursor') from None


def prepare_search(args):
    if not isinstance(args, dict) or not {'query'} <= set(args) or set(args) - {'query', 'prefix', 'limit', 'cursor'}:
        raise ToolError('invalid_arguments')
    query, prefix, limit = args['query'], args.get('prefix', ''), args.get('limit', 100)
    if (not isinstance(query, str) or not query or any(char in query for char in '\x00' + _LINE_BREAKS)
            or not isinstance(prefix, str) or type(limit) is not int or not 1 <= limit <= 200):
        raise ToolError('invalid_arguments')
    try:
        if len(query.encode('utf-8')) > MAX_QUERY_BYTES:
            raise ToolError('input_too_large')
    except UnicodeError:
        raise ToolError('invalid_data') from None
    _path_parts(prefix)
    normalized = {'query': query, 'prefix': prefix, 'limit': limit}
    if 'cursor' in args:
        _decode_cursor(args['cursor'])
        normalized['cursor'] = args['cursor']
    return {'name': 'search_files_page', 'args': normalized, 'policy': 'read'}


def _check_time(deadline):
    if time.monotonic() >= deadline:
        raise ToolError('tool_timeout')


def _match(path, number, line, column, query):
    # Center the first occurrence where practical; very long queries can exceed
    # the preview bound, so preserve their beginning and mark truncation.
    start = max(0, column - max(0, (MAX_PREVIEW_CHARS - len(query)) // 2))
    start = min(start, max(0, len(line) - MAX_PREVIEW_CHARS))
    end = min(len(line), start + MAX_PREVIEW_CHARS)
    return {'path': path, 'line': number, 'column': column + 1,
            'text': line[start:end], 'preview_start_column': start + 1,
            'line_chars': len(line), 'preview_truncated': start != 0 or end != len(line)}


def execute_search(tools, args, timeout):
    args = prepare_search(args)['args']
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ToolError('tool_timeout')
    deadline = time.monotonic() + timeout
    cursor = _decode_cursor(args['cursor']) if 'cursor' in args else None
    try:
        files, tree, root_identity = _scan(tools.root, args['prefix'], deadline)
        query_id = hashlib.sha256(json.dumps([str(tools.root), root_identity, args['query'],
                                            args['prefix'], args['limit']],
                                           ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
        if cursor and cursor['q'] != query_id:
            raise ToolError('invalid_cursor')
        offset = cursor['o'] if cursor else 0
        fingerprint = hashlib.sha256(tree.encode('ascii'))
        window, skipped_examples = [], []
        skipped_counts = {'binary': 0, 'file_too_large': 0}
        scanned_bytes, scanned_files, total_matches = 0, 0, 0
        for path in files:
            _check_time(deadline)
            reason = None
            try:
                with tools._parent_directory(path) as (directory, name):
                    data, _ = tools._read_at(directory, name, MAX_FILE_BYTES)
            except ToolError as error:
                if error.code != 'file_too_large':
                    raise
                reason = 'file_too_large'
            if reason is None:
                scanned_bytes += len(data)
                if scanned_bytes > MAX_SEARCH_BYTES:
                    raise ToolError('search_too_large')
                fingerprint.update(json.dumps([path, hashlib.sha256(data).hexdigest()], ensure_ascii=False).encode('utf-8'))
                try:
                    text = data.decode('utf-8')
                    if '\x00' in text:
                        reason = 'binary'
                except UnicodeError:
                    reason = 'binary'
            if reason:
                fingerprint.update(json.dumps([path, reason], ensure_ascii=False).encode('utf-8'))
                skipped_counts[reason] += 1
                if len(skipped_examples) < MAX_SKIP_EXAMPLES:
                    skipped_examples.append({'path': path, 'reason': reason})
                continue
            scanned_files += 1
            # Preserve interoperability with existing read_lines coordinates.
            for number, line in enumerate(text.splitlines(), 1):
                _check_time(deadline)
                column = line.find(args['query'])
                if column >= 0:
                    if total_matches >= offset and len(window) < args['limit']:
                        window.append(_match(path, number, line, column, args['query']))
                    total_matches += 1
        _check_time(deadline)
        after_files, after_tree, after_root = _scan(tools.root, args['prefix'], deadline)
        if after_tree != tree or after_files != files or after_root != root_identity:
            raise ToolError('tree_changed')
        observed = fingerprint.hexdigest()
        if cursor:
            if cursor['t'] != observed:
                raise ToolError('cursor_stale')
            if offset >= total_matches:
                raise ToolError('invalid_cursor')
        while True:
            _check_time(deadline)
            end = offset + len(window)
            eof = end == total_matches
            result = {'ok': True, 'matches': window,
                      'next_cursor': None if eof else _encode_cursor({'q': query_id, 't': observed, 'o': end}),
                      'eof': eof, 'total_matches': total_matches, 'scanned_files': scanned_files,
                      'scanned_bytes': scanned_bytes, 'skipped_counts': skipped_counts,
                      'skipped_examples': skipped_examples}
            if len(json.dumps({'result': result}, ensure_ascii=False, allow_nan=False).encode('utf-8')) + 1 <= MAX_BYTES:
                return result
            if not window:
                raise ToolError('response_too_large')
            window.pop()
            if not window and total_matches > offset:
                raise ToolError('response_too_large')
    except UnicodeError:
        raise ToolError('invalid_data') from None
    except FileNotFoundError:
        raise ToolError('cursor_stale' if cursor else 'not_found') from None
    except OSError:
        raise ToolError('file_io_error') from None
    except ToolError as error:
        if cursor and error.code in ('tree_changed', 'file_changed'):
            raise ToolError('cursor_stale') from None
        raise
