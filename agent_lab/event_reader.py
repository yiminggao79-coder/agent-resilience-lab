"""Bounded pages of committed metadata; never a task-history replay protocol."""
import hashlib
import json
import math
import re
import sqlite3
import sys
import time

from .store import FIELDS, StorageUnavailable

MAX_METADATA_BYTES = 8192
MAX_METADATA_DEPTH = 32
_STRING_FIELDS = frozenset({'phase', 'code', 'tool', 'operation_id', 'turn_id',
                            'request_id_hash', 'request_id', 'action_digest', 'diagnostic',
                            'native_job_id', 'finish_reason'})
_BOOLEAN_FIELDS = frozenset({'ok', 'recovered', 'local_quiescent'})
_FIELDS = frozenset(FIELDS)
_QUERY = """
SELECT seq,
 CASE WHEN typeof(at) IN ('integer','real') AND at BETWEEN 0 AND ? THEN at END,
 CASE WHEN typeof(kind)='text' AND length(CAST(kind AS BLOB)) BETWEEN 1 AND 48
           AND kind NOT GLOB '*[^a-z_]*' AND instr(kind,char(0))=0 THEN kind END,
 CASE WHEN typeof(meta)='text' AND length(CAST(meta AS BLOB))<=8192 THEN CAST(meta AS BLOB) END,
 CASE WHEN length(CAST(meta AS BLOB))>8192 THEN 1 ELSE 0 END
FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?
"""


def _invalid():
    return ValueError('invalid_event_query')


def _checked_identity(store):
    try:
        identity = store.checked_file_identity()
        if (not isinstance(identity, tuple) or len(identity) != 3 or not isinstance(identity[0], str)
                or any(type(value) is not int or value < 0 for value in identity[1:])):
            raise StorageUnavailable('storage_unavailable')
        return identity
    except (OSError, ValueError, TypeError, AttributeError):
        raise StorageUnavailable('storage_unavailable') from None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError()


def _check_depth(value):
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_METADATA_DEPTH:
            raise ValueError()
        children = current.values() if isinstance(current, dict) else current
        for child in children:
            if isinstance(child, (dict, list)):
                pending.append((child, depth + 1))


def _valid_value(key, value):
    if key=='source_sha256':return type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None
    if value is None:
        return True
    if key in _STRING_FIELDS:
        pattern = '[a-z_]{1,32}(,[a-z_]{1,32}){0,2}' if key == 'diagnostic' else '[a-z0-9_]{1,128}'
        return isinstance(value, str) and re.fullmatch(pattern, value) is not None
    if key in _BOOLEAN_FIELDS:
        return type(value) is bool
    if key == 'exit_code':
        # subprocess return codes are negative when a signal terminates it.
        return type(value) is int and -(1 << 63) <= value <= (1 << 63) - 1
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _metadata(raw, oversize):
    if oversize:
        return {}, ['metadata_too_large'], 0
    try:
        if not isinstance(raw, bytes):
            raise ValueError()
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_reject_constant)
        if not isinstance(value, dict):
            raise ValueError()
        _check_depth(value)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return {}, ['invalid_metadata'], 0
    metadata = {key: item for key, item in value.items() if key in _FIELDS and _valid_value(key, item)}
    filtered = len(value) - len(metadata)
    return metadata, ['filtered_metadata'] if filtered else [], filtered


def read_events(store, task_id, *, after=0, limit=100, expected_database=None):
    """Seek global sequence numbers for one task within one readonly snapshot.

    A later page observes a later transaction. The file fingerprint is a
    continuation guard, not authentication, liveness or an effect receipt.
    """
    if (not isinstance(task_id, str) or re.fullmatch('[0-9a-f]{32}', task_id) is None
            or type(after) is not int or not 0 <= after <= (1 << 63) - 1
            or type(limit) is not int or not 1 <= limit <= 100
            or (expected_database is not None and (not isinstance(expected_database, str)
                or re.fullmatch('[0-9a-f]{64}', expected_database) is None))):
        raise _invalid()
    try:
        if store.db.in_transaction:
            raise _invalid()
        identity = _checked_identity(store)
        fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=True,
                                                 separators=(',', ':')).encode('ascii')).hexdigest()
        if expected_database is not None and expected_database != fingerprint:
            raise _invalid()
        with store.db:
            store.db.execute('BEGIN')
            if not store.db.execute('SELECT 1 FROM tasks WHERE id=?', (task_id,)).fetchone():
                raise ValueError('task_not_found')
            rows = store.db.execute(_QUERY, (sys.float_info.max, task_id, after, limit + 1)).fetchall()
            if _checked_identity(store) != identity:
                raise StorageUnavailable('storage_unavailable')
    except sqlite3.Error:
        raise StorageUnavailable('storage_unavailable') from None
    events = []
    for seq, at, kind, raw, oversize in rows[:limit]:
        metadata, diagnostics, filtered = _metadata(raw, oversize)
        diagnostics = (['invalid_event_time'] if at is None else []) + (['invalid_event_kind'] if kind is None else []) + diagnostics
        events.append({'seq': seq, 'at': at, 'kind': kind, 'metadata': metadata,
                       'diagnostics': diagnostics, 'filtered_fields': filtered})
    return {'version': 1, 'task_id': task_id, 'database_identity': fingerprint, 'after': after,
            'last_seq': events[-1]['seq'] if events else after,
            'has_more': len(rows) > limit, 'events': events}


def follow_events(store, task_id, *, after=0, limit=100, expected_database=None,
                  seconds=10, max_pages=20):
    """Yield bounded live metadata observations, never inferred task liveness.

    The polling deadline does not interrupt a SQLite read or a slow consumer.
    Each page is its own committed snapshot; only its database source is pinned.
    """
    if (type(seconds) not in (int, float) or not 0 < seconds <= 30 or not math.isfinite(seconds)
            or type(max_pages) is not int or not 1 <= max_pages <= 100):
        raise ValueError('invalid_event_follow')
    deadline = time.monotonic() + seconds
    pages, count = 0, 0
    while True:
        if pages and time.monotonic() >= deadline:
            reason = 'deadline'
            break
        page = read_events(store, task_id, after=after, limit=limit,
                           expected_database=expected_database)
        expected_database = page['database_identity']
        after = page['last_seq']
        if page['events'] or pages == 0:
            pages += 1
            count += len(page['events'])
            yield {'type': 'event_page', **page}
        if pages >= max_pages:
            reason = 'page_limit'
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            reason = 'deadline'
            break
        if not page['has_more']:
            time.sleep(min(.25, remaining))
    yield {'type': 'event_follow_finished', 'task_id': task_id,
           'database_identity': expected_database, 'last_seq': after,
           'pages': pages, 'event_count': count, 'reason': reason}
