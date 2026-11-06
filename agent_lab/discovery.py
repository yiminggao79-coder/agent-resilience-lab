"""Bounded, stateless pagination over visible workspace file names.

Metadata fingerprints detect observed changes, not hostile filesystem snapshots.
"""
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import time

from .tools import MAX_BYTES, ToolError

MAX_ENTRIES = 10000
MAX_DEPTH = 32
MAX_SCAN_BYTES = 8 * 1024 * 1024
MAX_CURSOR_BYTES = 1024
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _encode_cursor(value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')
    return 'd1.' + base64.urlsafe_b64encode(data).decode('ascii')


def _decode_cursor(value):
    try:
        if not isinstance(value, str) or len(value) > MAX_CURSOR_BYTES or not value.startswith('d1.'):
            raise ValueError()
        data = base64.b64decode(value[3:], altchars=b'-_', validate=True)
        decoded = json.loads(data)
        if (not isinstance(decoded, dict) or set(decoded) != {'q', 't', 'o'}
                or type(decoded['o']) is not int or not 1 <= decoded['o'] <= MAX_ENTRIES
                or any(not isinstance(decoded[k], str) or not re.fullmatch('[0-9a-f]{64}', decoded[k]) for k in ('q', 't'))
                or _encode_cursor(decoded) != value):
            raise ValueError()
        return decoded
    except (ValueError, UnicodeError, binascii.Error, RecursionError):
        raise ToolError('invalid_cursor') from None


def _path_parts(value):
    if not isinstance(value, str) or len(value) > 512 or '\x00' in value or '\\' in value:
        raise ToolError('invalid_path')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise ToolError('invalid_data') from None
    if not value:
        return []
    parts = value.split('/')
    if any(not p or p in ('.', '..') or p.startswith('.') for p in parts):
        raise ToolError('permission_denied')
    if len(parts) > MAX_DEPTH:
        raise ToolError('listing_too_large')
    return parts


def prepare_discovery(args):
    if not isinstance(args, dict) or set(args) - {'prefix', 'limit', 'cursor'}:
        raise ToolError('invalid_arguments')
    prefix, limit = args.get('prefix', ''), args.get('limit', 100)
    if not isinstance(prefix, str) or type(limit) is not int or not 1 <= limit <= 200:
        raise ToolError('invalid_arguments')
    _path_parts(prefix)
    normalized = {'prefix': prefix, 'limit': limit}
    if 'cursor' in args:
        _decode_cursor(args['cursor'])
        normalized['cursor'] = args['cursor']
    return {'name': 'list_files_page', 'args': normalized, 'policy': 'read'}


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _scan(root, prefix, deadline):
    files, records, entries, size = [], [], 0, 0
    root_fd = os.open(str(root), _DIRECTORY_FLAGS)
    opened = [root_fd]
    ancestors = []
    root_info = os.fstat(root_fd)

    def check_time():
        if time.monotonic() >= deadline:
            raise ToolError('tool_timeout')

    def record(path, info):
        nonlocal size
        encoded = json.dumps([path, _identity(info)], ensure_ascii=False).encode('utf-8')
        size += len(encoded)
        if size > MAX_SCAN_BYTES:
            raise ToolError('listing_too_large')
        records.append((path, encoded))

    def open_child(directory, name, before):
        child = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory)
        try:
            info = os.fstat(child)
            if info.st_dev != root_info.st_dev:
                raise ToolError('permission_denied')
            if _identity(before) != _identity(info):
                raise ToolError('tree_changed')
            return child
        except BaseException:
            os.close(child)
            raise

    def visit(directory, relative, depth):
        nonlocal entries
        check_time()
        before = os.fstat(directory)
        record(relative, before)
        with os.scandir(directory) as items:
            for item in items:
                check_time()
                entries += 1
                if entries > MAX_ENTRIES:
                    raise ToolError('listing_too_large')
                if item.name.startswith('.'):
                    continue
                path = relative + '/' + item.name if relative else item.name
                info = os.stat(item.name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    continue
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1):
                    continue
                _path_parts(path)  # Invalid visible entries must fail, never vanish.
                if info.st_dev != root_info.st_dev:
                    raise ToolError('permission_denied')
                if stat.S_ISDIR(info.st_mode):
                    if depth >= MAX_DEPTH:
                        raise ToolError('listing_too_large')
                    child = open_child(directory, item.name, info)
                    try:
                        visit(child, path, depth + 1)
                        if _identity(os.stat(item.name, dir_fd=directory, follow_symlinks=False)) != _identity(os.fstat(child)):
                            raise ToolError('tree_changed')
                    finally:
                        os.close(child)
                else:
                    record(path, info)
                    files.append(path)
        if _identity(before) != _identity(os.fstat(directory)):
            raise ToolError('tree_changed')

    try:
        directory, current = root_fd, ''
        for part in _path_parts(prefix):
            check_time()
            before = os.fstat(directory)
            ancestors.append((directory, before, part))
            record(current, before)
            info = os.stat(part, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ToolError('permission_denied')
            if not stat.S_ISDIR(info.st_mode):
                raise ToolError('not_a_directory')
            directory = open_child(directory, part, info)
            opened.append(directory)
            current = current + '/' + part if current else part
        visit(directory, prefix, len(_path_parts(prefix)))
        for index, (parent, before, name) in enumerate(ancestors):
            if (_identity(before) != _identity(os.fstat(parent))
                    or _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _identity(os.fstat(opened[index + 1]))):
                raise ToolError('tree_changed')
        if _identity(os.stat(root, follow_symlinks=False)) != _identity(os.fstat(root_fd)):
            raise ToolError('tree_changed')
        fingerprint = hashlib.sha256()
        for _, encoded in sorted(records):
            fingerprint.update(encoded)
            fingerprint.update(b'\n')
        return sorted(files), fingerprint.hexdigest(), (root_info.st_dev, root_info.st_ino)
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def execute_discovery(root, args, timeout):
    args = prepare_discovery(args)['args']
    deadline = time.monotonic() + max(0, timeout)
    try:
        files, tree, root_identity = _scan(root, args['prefix'], deadline)
    except UnicodeError:
        raise ToolError('invalid_data') from None
    except FileNotFoundError:
        raise ToolError('cursor_stale' if 'cursor' in args else 'not_found') from None
    except OSError:
        raise ToolError('file_io_error') from None
    query = hashlib.sha256(json.dumps([str(root), root_identity, args['prefix'], args['limit']]).encode('utf-8')).hexdigest()
    offset = 0
    if 'cursor' in args:
        cursor = _decode_cursor(args['cursor'])
        if cursor['q'] != query:
            raise ToolError('invalid_cursor')
        if cursor['t'] != tree:
            raise ToolError('cursor_stale')
        offset = cursor['o']
        if offset >= len(files):
            raise ToolError('invalid_cursor')
    end = min(offset + args['limit'], len(files))
    while True:
        eof = end == len(files)
        result = {'ok': True, 'files': files[offset:end],
                  'next_cursor': None if eof else _encode_cursor({'q': query, 't': tree, 'o': end}),
                  'eof': eof, 'total_files': len(files)}
        # worker_main prints this envelope followed by one newline.
        if len(json.dumps({'result': result}, ensure_ascii=False, allow_nan=False).encode('utf-8')) + 1 <= MAX_BYTES:
            return result
        end -= 1
        if end <= offset:
            raise ToolError('response_too_large')
