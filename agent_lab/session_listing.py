"""Readonly bounded metadata pages; JSON histories stay inside SQLite."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat

from .store import StorageUnavailable

PHASES = ('ready', 'model_pending', 'tool_queued', 'tool_pending', 'awaiting_approval',
          'needs_confirmation', 'completed', 'failed')
SORT = 'created_at_desc_task_id_desc'
_MAX_TIME = 253402300799
_CURSOR_FIELDS = {'v', 'sort', 'scope', 'at', 'id'}
_CANONICAL_ROW_ID = "typeof(id)='text' AND length(CAST(id AS BLOB))=32 AND id NOT GLOB '*[^0-9a-f]*' AND instr(id,char(0))=0"


def _invalid():
    return ValueError('invalid_session_listing')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _id(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value) is not None


def _timestamp(value, *, anchor=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and ((anchor and value == -1) or 0 <= value <= _MAX_TIME))


def _workspace(value):
    if value is None:
        return None
    try:
        if (not isinstance(value, (str, Path)) or not str(value) or '\x00' in str(value)
                or len(str(value).encode('utf-8')) > 4096):
            raise _invalid()
        return str(Path(value).resolve())
    except (ValueError, OSError, RuntimeError, UnicodeError):
        raise _invalid() from None


def _identity(store):
    try:
        path = Path(store.path)
        if path.is_symlink():
            raise OSError()
        path = path.resolve(strict=True)
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise OSError()
        identity = str(path), info.st_dev, info.st_ino
        if identity != getattr(store, '_database_file_identity', None):
            raise OSError()
        return identity
    except (OSError, ValueError, RuntimeError):
        raise StorageUnavailable('storage_unavailable') from None


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise _invalid()
        value[key] = item
    return value


def _encode_cursor(payload):
    return base64.urlsafe_b64encode(_json(payload).encode('ascii')).decode('ascii').rstrip('=')


def _decode_cursor(cursor, scope):
    if cursor is None:
        return None
    try:
        if (not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024
                or re.fullmatch('[A-Za-z0-9_-]+', cursor) is None):
            raise _invalid()
        raw = base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True)
        payload = json.loads(raw.decode('ascii'), object_pairs_hook=_unique)
        if (not isinstance(payload, dict) or set(payload) != _CURSOR_FIELDS
                or type(payload['v']) is not int or payload['v'] != 1
                or payload['sort'] != SORT or payload['scope'] != scope
                or not _timestamp(payload['at'], anchor=True) or not _id(payload['id'])
                or _encode_cursor(payload) != cursor):
            raise _invalid()
        return payload['at'], payload['id']
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
        raise _invalid() from None


def _sql_id(field):
    value = "json_extract(doc,'$.%s')" % field
    return ("CASE WHEN json_type(doc,'$.%s')='text' AND length(CAST(%s AS BLOB))=32 "
            "AND %s NOT GLOB '*[^0-9a-f]*' AND instr(%s,char(0))=0 AND %s THEN %s END"
            % (field, value, value, value, _sql_nul_free(field), value))


def _sql_nul_free(field):
    # SQLite 3.35 JSON1 truncates scalar text at an escaped NUL. Multi-path
    # extraction preserves the JSON token, so check it without returning it.
    return ("instr(replace(json_extract(doc,'$.%s','$.%s'),char(92)||char(92),''),"
            "char(92)||'u0000')=0" % (field, field))


# CASE is deliberately nested: invalid JSON must never reach json_type/extract.
_QUERY = """
WITH source AS (
 SELECT id AS task_id, json_valid(state) AS valid_json,
 CASE WHEN json_valid(state) THEN CASE WHEN json_type(state)='object' THEN state ELSE '{}' END ELSE '{}' END AS doc
 FROM tasks WHERE %s
), scalar AS (
 SELECT task_id,valid_json,doc,
 %s AS state_id,%s AS session_id,%s AS turn_id,
 CASE WHEN json_type(doc,'$.phase')='text' AND %s AND json_extract(doc,'$.phase') IN (%s)
      THEN json_extract(doc,'$.phase') END AS phase,
 CASE WHEN json_type(doc,'$.profile')='text' AND %s AND json_extract(doc,'$.profile') IN ('coding','local')
      THEN json_extract(doc,'$.profile') END AS profile,
 CASE WHEN json_type(doc,'$.created_at') IN ('integer','real')
           AND json_extract(doc,'$.created_at') BETWEEN 0 AND %d
      THEN json_extract(doc,'$.created_at') END AS created_at
 FROM source
), projected AS (
 SELECT task_id,session_id,turn_id,phase,profile,created_at,doc,
 COALESCE(created_at,-1) AS sort_at,
 CASE WHEN NOT valid_json THEN 'invalid_json'
      WHEN state_id IS NOT NULL AND state_id!=task_id THEN 'task_id_mismatch'
      WHEN state_id IS NULL OR session_id IS NULL OR turn_id IS NULL OR phase IS NULL
           OR profile IS NULL OR created_at IS NULL THEN 'incomplete_metadata' END AS metadata_error
 FROM scalar
)
SELECT task_id,session_id,turn_id,phase,profile,created_at,metadata_error,sort_at
FROM projected
WHERE (? IS NULL OR (json_type(doc,'$.workspace')='text' AND %s AND json_extract(doc,'$.workspace')=?))
  AND (? IS NULL OR phase=?)
  AND (?=0 OR sort_at<? OR (sort_at=? AND task_id<?))
ORDER BY sort_at DESC,task_id DESC LIMIT ?
""" % (_CANONICAL_ROW_ID, _sql_id('id'), _sql_id('session_id'), _sql_id('turn_id'),
       _sql_nul_free('phase'), ','.join("'%s'" % phase for phase in PHASES),
       _sql_nul_free('profile'), _MAX_TIME, _sql_nul_free('workspace'))


def list_sessions(store, *, limit=20, cursor=None, workspace=None, phase=None):
    """Read one page in one transaction; later pages are fresh observations.

    SQLite scans/sorts existing JSON rows: this is O(n), not an indexed listing.
    Cursor scope hashes paths; it is neither authentication nor encryption.
    """
    if type(limit) is not int or not 1 <= limit <= 100 or (phase is not None and phase not in PHASES):
        raise _invalid()
    workspace = _workspace(workspace)
    identity = _identity(store)
    scope = hashlib.sha256(_json([identity, workspace, phase]).encode('ascii')).hexdigest()
    anchor = _decode_cursor(cursor, scope)
    at, task_id = anchor if anchor is not None else (-1, '')
    try:
        # Never commit caller-owned work as an incidental listing side effect.
        if store.db.in_transaction:
            raise _invalid()
        with store.db:
            store.db.execute('BEGIN')
            excluded = store.db.execute('SELECT COUNT(*) FROM tasks WHERE NOT (' + _CANONICAL_ROW_ID + ')').fetchone()[0]
            rows = store.db.execute(_QUERY, (workspace, workspace, phase, phase, int(anchor is not None),
                                             at, at, task_id, limit + 1)).fetchall()
            if _identity(store) != identity:
                raise StorageUnavailable('storage_unavailable')
    except sqlite3.Error:
        raise StorageUnavailable('storage_unavailable') from None
    items = [dict(zip(('task_id', 'session_id', 'turn_id', 'phase', 'profile', 'created_at', 'metadata_error'), row[:7]))
             for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = _encode_cursor({'v': 1, 'sort': SORT, 'scope': scope, 'at': last[7], 'id': last[0]})
    return {'items': items, 'next_cursor': next_cursor, 'sort': SORT,
            'excluded_noncanonical_ids': excluded, 'history_checked': False,
            'process_liveness': 'not_checked'}
