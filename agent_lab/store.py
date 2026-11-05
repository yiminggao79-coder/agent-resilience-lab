"""Atomic task snapshots plus metadata-only traces, with cooperative OS locks."""
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid
import hashlib
from urllib.parse import quote

FIELDS = {'phase', 'code', 'error_category', 'tool', 'duration_ms', 'steps', 'attempt', 'input_tokens',
          'output_tokens', 'cost_usd', 'reserved_usd', 'recovered', 'ok', 'exit_code',
          'request_bytes', 'response_bytes', 'model_calls', 'operation_id', 'revision', 'turn_id', 'plan_revision',
          'checked_files', 'file_notices','source_sha256','covered_messages','compaction_generation',
          'summary_sha256','compaction_automatic',
          'request_id_hash', 'request_id', 'action_digest', 'http_status', 'diagnostic', 'response_body_bytes', 'finish_reason', 'retained_bytes', 'original_bytes', 'unstarted_calls', 'local_quiescent', 'native_job_id'}


class Busy(Exception):
    pass


class StopRequested(Exception):
    """A durable stop request won the next-operation claim transaction."""
    pass


class InputsChanged(Exception):
    """A sender withdrew valid selected feedback before application committed."""
    pass


class InputsPending(Exception):
    """Finalization raced with durable feedback awaiting a safe boundary."""
    pass


class StorageUnavailable(Exception):
    """Public storage fault, deliberately excludes SQL/path/credential text."""
    pass


def _database_file_identity(path):
    """In-memory provenance for readers; never a path-based liveness claim."""
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise OSError('invalid_database_file')
    return str(path), info.st_dev, info.st_ino


class Store:
    @classmethod
    def open_readonly(cls, path):
        """Inspect existing state without creation, chmod or schema migration."""
        path = Path(path).absolute()
        if path.is_symlink() or not path.is_file():
            raise ValueError('state_not_found')
        instance = cls.__new__(cls)
        instance.path = path.resolve()
        try:
            instance._database_file_identity = _database_file_identity(instance.path)
            instance.db = sqlite3.connect('file:' + quote(str(instance.path), safe='/') + '?mode=ro',
                                          uri=True, timeout=2)
            instance.db.execute('PRAGMA query_only=ON')
            if _database_file_identity(instance.path) != instance._database_file_identity:
                raise OSError('database_file_changed')
        except (sqlite3.Error, OSError):
            if hasattr(instance, 'db'):
                instance.db.close()
            raise StorageUnavailable('storage_unavailable') from None
        return instance

    def __init__(self, path):
        path = Path(path).absolute()
        if path.is_symlink():
            raise ValueError('state_symlink')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = path.resolve()
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        os.chmod(str(self.path), 0o600)
        self._database_file_identity = _database_file_identity(self.path)
        self.db = sqlite3.connect(str(self.path), timeout=2)
        try:
            if _database_file_identity(self.path) != self._database_file_identity:
                raise OSError('database_file_changed')
        except OSError:
            self.db.close()
            raise StorageUnavailable('storage_unavailable') from None
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.executescript('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, state TEXT NOT NULL);'
                              'CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, at REAL NOT NULL, kind TEXT NOT NULL, meta TEXT NOT NULL);'
                              'CREATE TABLE IF NOT EXISTS turns (seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, turn_id TEXT NOT NULL, state TEXT NOT NULL, UNIQUE(task_id,turn_id));'
                              'CREATE TABLE IF NOT EXISTS steering_inputs (seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, request_id TEXT NOT NULL, turn_id TEXT NOT NULL, text TEXT NOT NULL, text_bytes INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN (\'pending\',\'applied\',\'withdrawn\')), message_index INTEGER, applied_revision INTEGER, UNIQUE(task_id,request_id));'
                              'CREATE INDEX IF NOT EXISTS steering_pending ON steering_inputs(task_id,status);'
                              'CREATE TABLE IF NOT EXISTS stop_requests (task_id TEXT NOT NULL,turn_id TEXT NOT NULL,request_id TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN (\'requested\',\'observed\')),observed_phase TEXT,observed_reason TEXT,PRIMARY KEY(task_id,turn_id),UNIQUE(task_id,request_id));')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(events)')}
        if 'event_id' not in columns:
            self.db.execute('ALTER TABLE events ADD COLUMN event_id TEXT')
        self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS event_identity ON events(event_id)')
        self.db.commit()

    def save(self, state, kind, meta=None, *, applied_inputs=(), require_empty_inbox=False,
             require_no_stop=False, observed_stop=None):
        meta = meta or {}
        if not re.fullmatch('[a-z_]{1,48}', kind) or set(meta) - FIELDS:
            raise ValueError('invalid_trace_metadata')
        for key, value in meta.items():
            if key=='source_sha256' and (type(value) is not str or re.fullmatch('[a-f0-9]{64}',value) is None):
                raise ValueError('invalid_trace_metadata')
            if isinstance(value, str):
                pattern = '[a-z_]{1,32}(,[a-z_]{1,32}){0,2}' if key == 'diagnostic' else '[a-z0-9_]{1,128}'
                if not re.fullmatch(pattern, value):
                    raise ValueError('invalid_trace_string')
            elif value is not None and (type(value) not in (int, float, bool) or not math.isfinite(value)):
                raise ValueError('invalid_trace_number')
        snapshot = json.dumps(state, ensure_ascii=False, allow_nan=False)
        event_id = (hashlib.sha256((state['_event_id'] + kind + snapshot).encode()).hexdigest()
                    if state.get('_event_id') else uuid.uuid4().hex)
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                if self.db.execute('SELECT 1 FROM events WHERE event_id=?', (event_id,)).fetchone():
                    return
                if require_no_stop and self.db.execute(
                        "SELECT 1 FROM stop_requests WHERE task_id=? AND turn_id=? AND status='requested'",
                        (state['id'], state.get('turn_id'))).fetchone():
                    raise StopRequested('stop_requested')
                previous = self.db.execute('SELECT state FROM tasks WHERE id=?', (state['id'],)).fetchone()
                previous = json.loads(previous[0]) if previous else None
                if require_empty_inbox and self.db.execute(
                        'SELECT 1 FROM steering_inputs WHERE task_id=? AND turn_id=? AND status=\'pending\' LIMIT 1',
                        (state['id'], state.get('turn_id'))).fetchone():
                    raise InputsPending('inputs_pending')
                if observed_stop is not None:
                    self._observe_stop(state, previous, observed_stop)
                self._apply_inputs(state, previous, applied_inputs)
                if previous:
                    if previous.get('turn_id') != state.get('turn_id'):
                        # Also archives pre-turn-table snapshots on their first follow-up.
                        self._save_turn(previous)
                self._save_turn(state)
                self.db.execute('INSERT INTO tasks(id,state) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state', (state['id'], snapshot))
                self.db.execute('INSERT INTO events(task_id,at,kind,meta,event_id) VALUES(?,?,?,?,?)', (state['id'], time.time(), kind, json.dumps(meta, allow_nan=False), event_id))
        except sqlite3.Error:
            raise StorageUnavailable('storage_unavailable') from None

    @staticmethod
    def _stop_receipt(row):
        return dict(zip(('request_id', 'turn_id', 'status', 'observed_phase', 'observed_reason'), row))

    def stop_request(self, task_id, turn_id):
        """Read only control metadata, including absent-table legacy databases."""
        self._input_id(task_id)
        self._input_id(turn_id)
        try:
            if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stop_requests'").fetchone():
                return None
            row = self.db.execute(
                'SELECT request_id,turn_id,status,observed_phase,observed_reason FROM stop_requests WHERE task_id=? AND turn_id=?',
                (task_id, turn_id)).fetchone()
            return self._stop_receipt(row) if row else None
        except sqlite3.Error:
            raise StorageUnavailable('storage_unavailable') from None

    def request_stop(self, task_id, turn_id, request_id, workspace):
        for value in (task_id, turn_id, request_id):
            self._input_id(value)
        try:
            selected_workspace = str(Path(workspace).resolve())
        except (TypeError, ValueError, OSError, RuntimeError):
            raise ValueError('stop_workspace_mismatch') from None
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                state = self.load(task_id)
                if state.get('workspace') != selected_workspace:
                    raise ValueError('stop_workspace_mismatch')
                existing = self.db.execute(
                    'SELECT request_id,turn_id,status,observed_phase,observed_reason FROM stop_requests WHERE task_id=? AND request_id=?',
                    (task_id, request_id)).fetchone()
                if existing:
                    if existing[1] != turn_id:
                        raise ValueError('stop_request_conflict')
                    return self._stop_receipt(existing)
                if state.get('turn_id') != turn_id:
                    raise ValueError('stop_turn_mismatch')
                if (state.get('phase') not in {'ready', 'model_pending', 'tool_queued', 'tool_pending', 'awaiting_approval'}
                        or state.get('turn_completed_at') is not None
                        or (state.get('model_purpose') == 'semantic_compaction'
                            and state.get('last_compaction', {}).get('origin_phase') != 'ready')):
                    raise ValueError('stop_not_active')
                existing = self.stop_request(task_id, turn_id)
                if existing:
                    return existing
                self.db.execute('INSERT INTO stop_requests(task_id,turn_id,request_id,status) VALUES(?,?,?,?)',
                                (task_id, turn_id, request_id, 'requested'))
                self._input_event(task_id, turn_id, request_id, 'stop_requested', 'requested')
                return {'request_id': request_id, 'turn_id': turn_id, 'status': 'requested',
                        'observed_phase': None, 'observed_reason': None}
        except sqlite3.Error:
            raise StorageUnavailable('storage_unavailable') from None

    def _observe_stop(self, state, previous, request_id):
        self._input_id(request_id)
        row = self.db.execute(
            'SELECT request_id,status FROM stop_requests WHERE task_id=? AND turn_id=?',
            (state['id'], state.get('turn_id'))).fetchone()
        reason = state.get('reason')
        if (not previous or previous.get('turn_id') != state.get('turn_id')
                or not row or row != (request_id, 'requested')
                or state.get('phase') not in {'completed', 'failed', 'needs_confirmation'}
                or (reason is not None and (not isinstance(reason, str) or re.fullmatch('[a-z0-9_]{1,128}', reason) is None))):
            raise ValueError('invalid_stop_observation')
        self.db.execute("UPDATE stop_requests SET status='observed',observed_phase=?,observed_reason=? WHERE task_id=? AND turn_id=? AND request_id=? AND status='requested'",
                        (state['phase'], reason, state['id'], state['turn_id'], request_id))

    @staticmethod
    def _input_id(value):
        if not isinstance(value, str) or re.fullmatch('[0-9a-f]{32}', value) is None:
            raise ValueError('invalid_input_id')

    @staticmethod
    def _receipt(row):
        return dict(zip(('request_id', 'turn_id', 'status', 'message_index', 'applied_revision'), row))

    def _input_event(self, task_id, turn_id, request_id, kind, status):
        self.db.execute('INSERT INTO events(task_id,at,kind,meta,event_id) VALUES(?,?,?,?,?)',
                        (task_id, time.time(), kind,
                         json.dumps({'request_id': request_id, 'turn_id': turn_id, 'code': status}), uuid.uuid4().hex))

    def queue_input(self, task_id, turn_id, text, request_id):
        for value in (task_id, turn_id, request_id):
            self._input_id(value)
        try:
            if not isinstance(text, str) or not text.strip() or not 1 <= len(text.encode('utf-8')) <= 65536:
                raise ValueError('invalid_input_text')
        except UnicodeError:
            raise ValueError('invalid_input_text') from None
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                existing = self.db.execute(
                    'SELECT request_id,turn_id,status,message_index,applied_revision,text FROM steering_inputs WHERE task_id=? AND request_id=?',
                    (task_id, request_id)).fetchone()
                if existing:
                    if existing[1] != turn_id or existing[5] != text:
                        raise ValueError('input_request_conflict')
                    return self._receipt(existing[:5])
                state = self.load(task_id)
                if state.get('turn_id') != turn_id:
                    raise ValueError('input_turn_mismatch')
                if (state.get('phase') not in {'ready', 'model_pending', 'tool_queued', 'tool_pending', 'awaiting_approval'}
                        or state.get('turn_completed_at') is not None
                        or (state.get('model_purpose') == 'semantic_compaction'
                            and state.get('last_compaction', {}).get('origin_phase') != 'ready')
                        or state.get('usage_unknown') or state.get('outcome_unresolved')):
                    raise ValueError('input_not_active')
                count, size = self.db.execute(
                    "SELECT count(*),coalesce(sum(text_bytes),0) FROM steering_inputs WHERE task_id=? AND status='pending'",
                    (task_id,)).fetchone()
                lifetime = self.db.execute('SELECT count(*) FROM steering_inputs WHERE task_id=? AND turn_id=?',
                                           (task_id, turn_id)).fetchone()[0]
                if count >= 32 or size + len(text.encode('utf-8')) > 1024 * 1024 or lifetime >= 128:
                    raise ValueError('input_capacity_exceeded')
                self.db.execute('INSERT INTO steering_inputs(task_id,request_id,turn_id,text,text_bytes,status) VALUES(?,?,?,?,?,?)',
                                (task_id, request_id, turn_id, text, len(text.encode('utf-8')), 'pending'))
                self._input_event(task_id, turn_id, request_id, 'input_queued', 'pending')
                return {'request_id': request_id, 'turn_id': turn_id, 'status': 'pending',
                        'message_index': None, 'applied_revision': None}
        except sqlite3.Error:
            raise StorageUnavailable('storage_unavailable') from None

    def inputs(self, task_id, include_text=False):
        self._input_id(task_id)
        if type(include_text) is not bool:
            raise ValueError('invalid_input_view')
        try:
            self.load(task_id)
            rows = self.db.execute(
                'SELECT request_id,turn_id,status,message_index,applied_revision,text FROM steering_inputs WHERE task_id=? ORDER BY seq',
                (task_id,)).fetchall()
            return [{**self._receipt(row[:5]), **({'text': row[5]} if include_text else {})} for row in rows]
        except sqlite3.Error:
            raise StorageUnavailable('storage_unavailable') from None

    def pending_inputs(self, task_id, turn_id):
        self._input_id(task_id)
        self._input_id(turn_id)
        try:
            return [{'request_id': rid, 'text': text} for rid, text in self.db.execute(
                "SELECT request_id,text FROM steering_inputs WHERE task_id=? AND turn_id=? AND status='pending' ORDER BY seq",
                (task_id, turn_id))]
        except sqlite3.Error:
            raise StorageUnavailable('storage_unavailable') from None

    def diagnostic_view(self, task_id):
        """One read snapshot; no workspace lock, liveness or effect verification."""
        try:
            with self.db:
                self.db.execute('BEGIN')
                state = self.load(task_id)
                rows = []
                if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='steering_inputs'").fetchone():
                    rows = self.db.execute(
                        "SELECT request_id,turn_id,status,message_index,applied_revision FROM steering_inputs "
                        "WHERE task_id=? AND status='pending' ORDER BY seq", (task_id,)).fetchall()
                return state, [self._receipt(row) for row in rows]
        except sqlite3.Error:
            raise StorageUnavailable('storage_unavailable') from None

    def withdraw_input(self, task_id, request_id):
        self._input_id(task_id)
        self._input_id(request_id)
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                row = self.db.execute(
                    'SELECT request_id,turn_id,status,message_index,applied_revision FROM steering_inputs WHERE task_id=? AND request_id=?',
                    (task_id, request_id)).fetchone()
                if not row:
                    raise ValueError('input_not_found')
                if row[2] == 'applied':
                    raise ValueError('input_already_applied')
                if row[2] == 'withdrawn':
                    return self._receipt(row)
                self.db.execute("UPDATE steering_inputs SET status='withdrawn' WHERE task_id=? AND request_id=? AND status='pending'",
                                (task_id, request_id))
                self._input_event(task_id, row[1], request_id, 'input_withdrawn', 'withdrawn')
                return self._receipt((row[0], row[1], 'withdrawn', row[3], row[4]))
        except sqlite3.Error:
            raise StorageUnavailable('storage_unavailable') from None

    def _apply_inputs(self, state, previous, applied_inputs):
        if not applied_inputs:
            return
        if not isinstance(applied_inputs, (list, tuple)):
            raise ValueError('invalid_applied_inputs')
        if (not previous or previous.get('turn_id') != state.get('turn_id')
                or type(state.get('revision')) is not int or state['revision'] <= previous.get('revision', -1)
                or not isinstance(previous.get('messages'), list) or not isinstance(state.get('messages'), list)
                or state['messages'][:len(previous['messages'])] != previous['messages']):
            raise ValueError('invalid_applied_inputs')
        selected, updates = [], []
        for offset, pair in enumerate(applied_inputs):
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ValueError('invalid_applied_inputs')
            request_id, index = pair
            self._input_id(request_id)
            row = self.db.execute(
                'SELECT turn_id,status,text FROM steering_inputs WHERE task_id=? AND request_id=?',
                (state['id'], request_id)).fetchone()
            if (not row or row[0] != state.get('turn_id') or row[1] == 'applied'
                    or type(index) is not int or index != len(previous['messages']) + offset
                    or index >= len(state['messages'])
                    or state['messages'][index] != {'role': 'user', 'content': row[2]}):
                raise ValueError('invalid_applied_inputs')
            selected.append((request_id, row[1]))
            updates.append((index, state['revision'], state['id'], request_id))
        if len({rid for rid, _ in selected}) != len(selected):
            raise ValueError('invalid_applied_inputs')
        if any(status == 'withdrawn' for _, status in selected):
            raise InputsChanged('inputs_changed')
        pending = self.db.execute(
            "SELECT request_id FROM steering_inputs WHERE task_id=? AND turn_id=? AND status='pending' ORDER BY seq LIMIT ?",
            (state['id'], state.get('turn_id'), len(selected))).fetchall()
        if [rid for rid, _ in selected] != [row[0] for row in pending]:
            raise ValueError('invalid_applied_inputs')
        # Validate every row/message before touching any receipt. The surrounding
        # BEGIN IMMEDIATE also serializes withdrawal/admission with this commit.
        self.db.executemany("UPDATE steering_inputs SET status='applied',message_index=?,applied_revision=? WHERE task_id=? AND request_id=? AND status='pending'",
                            updates)

    @staticmethod
    def _turn_record(state):
        if not state.get('turn_id') or 'messages' not in state:
            return None
        return {'task_id': state['id'], 'session_id': state.get('session_id'), 'turn_id': state['turn_id'],
                'phase': state['phase'], 'reason': state.get('reason'),
                'started_at': state.get('turn_started_at', state['created_at']),
                'completed_at': state.get('turn_completed_at'),
                'message_start': state.get('turn_message_start', 0), 'message_end': len(state['messages']),
                'limits': state['limits'], 'metrics': state['metrics'],
                'verified_success': state.get('verified_success')}

    def _save_turn(self, state):
        record = self._turn_record(state)
        if record is not None:
            self.db.execute('INSERT INTO turns(task_id,turn_id,state) VALUES(?,?,?) '
                            'ON CONFLICT(task_id,turn_id) DO UPDATE SET state=excluded.state',
                            (state['id'], state['turn_id'], json.dumps(record, ensure_ascii=False, allow_nan=False)))

    def load(self, task_id):
        row = self.db.execute('SELECT state FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise KeyError('task_not_found')
        return json.loads(row[0])

    def checked_file_identity(self):
        """Internal reader provenance; callers must not publish the raw path."""
        try:
            identity = _database_file_identity(self.path)
            if identity != getattr(self, '_database_file_identity', None):
                raise OSError('database_file_changed')
            return identity
        except OSError:
            raise StorageUnavailable('storage_unavailable') from None

    def events(self, task_id):
        return [{'seq': seq, 'at': at, 'kind': kind, **json.loads(meta)}
                for seq, at, kind, meta in self.db.execute('SELECT seq,at,kind,meta FROM events WHERE task_id=? ORDER BY seq', (task_id,))]

    def turns(self, task_id):
        """Ordered turn outcomes and budgets; transcript stays in the task snapshot."""
        rows = self.db.execute('SELECT state FROM turns WHERE task_id=? ORDER BY seq', (task_id,)).fetchall()
        if rows:
            return [json.loads(row[0]) for row in rows]
        # Older databases become queryable without rewriting their task history.
        record = self._turn_record(self.load(task_id))
        return [record] if record else []

    @contextmanager
    def lock(self, root):
        root = Path(root).resolve()
        if self.path == root or root in self.path.parents:
            raise ValueError('state_must_be_outside_workspace')
        fds = []
        try:
            for path in (self.path.with_suffix(self.path.suffix + '.lock'), root / '.agent-lab.lock'):
                fd = os.open(str(path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                fds.append(fd)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Busy('runner_busy')
            from .native_jobs import ensure_workspace_idle
            ensure_workspace_idle(root, self.path)
            from .process_guard import rotate_generation, ExecutionBusy
            try:
                rotate_generation(root)
            except ExecutionBusy:
                raise Busy('worker_active') from None
            yield
        finally:
            for fd in reversed(fds):
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
