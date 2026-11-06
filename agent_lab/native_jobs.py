"""Durable admission quarantine for host-managed native jobs; no process scanner."""


class JobStateError(Exception):
    def __init__(self, code='native_job_state_unavailable'):
        self.code = code if code in {'native_job_state_unavailable', 'native_job_reference_invalid',
                                    'native_job_source_mismatch', 'native_job_root_unsupported',
                                    'native_job_proof_invalid', 'workspace_quarantined'} else 'native_job_state_unavailable'
        super().__init__(self.code)


class JobBlocked(JobStateError):
    def __init__(self):
        super().__init__('workspace_quarantined')

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid
from urllib.parse import quote

MARKER = '.agent-lab.native-job'
BACKEND = 'macos_nofork_v1'
PROOFS = frozenset({'all_native_joined', 'no_supervisor_launch', 'worker_launch_failed'})
MAX_MARKER_BYTES = 16384
_LOCK_WAIT_SECONDS = 1.0
_REF_FIELDS = frozenset({'version', 'job_id', 'root', 'database', 'task_id', 'turn_id',
                         'operation_id', 'generation', 'intent_sha256', 'root_dev', 'root_ino', 'backend'})


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _reference_shape(reference):
    if not isinstance(reference, dict) or set(reference) != _REF_FIELDS:
        raise JobStateError('native_job_reference_invalid')
    if (type(reference['version']) is not int or reference['version'] != 1 or reference['backend'] != BACKEND
            or any(not isinstance(reference[key], str) or re.fullmatch('[0-9a-f]{32}', reference[key]) is None
                   for key in ('job_id', 'task_id', 'turn_id', 'operation_id', 'generation'))
            or not isinstance(reference['intent_sha256'], str)
            or re.fullmatch('[0-9a-f]{64}', reference['intent_sha256']) is None
            or any(type(reference[key]) is not int or reference[key] < 0 for key in ('root_dev', 'root_ino'))
            or any(not isinstance(reference[key], str) or not reference[key] or len(reference[key]) > 4096
                   or not Path(reference[key]).is_absolute() or '\x00' in reference[key] for key in ('root', 'database'))):
        raise JobStateError('native_job_reference_invalid')
    return dict(reference)


@contextmanager
def _root(root):
    descriptor = None
    try:
        path = Path(root).absolute()
        if path.is_symlink():
            raise JobStateError('native_job_reference_invalid')
        path = path.resolve()
        descriptor = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        yield path, descriptor, (info.st_dev, info.st_ino)
        after = os.stat(path, follow_symlinks=False)
        if (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino):
            raise JobStateError('native_job_reference_invalid')
    except (OSError, ValueError, TypeError, RuntimeError):
        raise JobStateError('native_job_state_unavailable') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _private(info):
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or mode & 0o7077 or not mode & 0o400):
        raise JobStateError('native_job_reference_invalid')


@contextmanager
def _marker_lock(directory):
    # Distinct from the execution lease: zero-launch receipts can be published
    # after the worker has released that lease. Never replace this lock inode.
    descriptor = os.open(MARKER + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
    try:
        _private(os.fstat(descriptor))
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise JobBlocked() from None
                time.sleep(min(.01, remaining))
        yield
    finally:
        os.close(descriptor)


def _database_path(database, root):
    path = Path(database).absolute()
    if path.is_symlink():
        raise JobStateError('native_job_reference_invalid')
    path = path.resolve()
    if path == root or root in path.parents:
        raise JobStateError('native_job_reference_invalid')
    return path


@contextmanager
def _connection(database, root, *, readonly):
    connection = None
    try:
        path = _database_path(database, root)
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            _private(os.fstat(descriptor))
        finally:
            os.close(descriptor)
        connection = sqlite3.connect('file:' + quote(str(path), safe='/') + ('?mode=ro' if readonly else '?mode=rw'),
                                     uri=True, timeout=2)
        if readonly:
            connection.execute('PRAGMA query_only=ON')
        else:
            connection.execute('PRAGMA synchronous=FULL')
        yield connection
    except (OSError, sqlite3.Error, ValueError, TypeError, UnicodeError):
        raise JobStateError('native_job_state_unavailable') from None
    finally:
        if connection is not None:
            connection.close()


def _read_marker(directory, root, identity):
    try:
        descriptor = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        _private(before)
        if before.st_size > MAX_MARKER_BYTES:
            raise JobStateError('native_job_reference_invalid')
        raw = stream.read(MAX_MARKER_BYTES + 1)
        after = os.fstat(stream.fileno())
        if ((before.st_dev, before.st_ino, before.st_mode, before.st_nlink, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_mode, after.st_nlink, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise JobStateError('native_job_reference_invalid')
    try:
        marker = json.loads(raw.decode('ascii'), object_pairs_hook=_unique)
        if (not isinstance(marker, dict) or set(marker) != _REF_FIELDS | {'state', 'proof_kind'}
                or raw != (_json(marker) + '\n').encode('ascii')):
            raise ValueError()
        reference = _reference_shape({key: marker[key] for key in _REF_FIELDS})
        if (reference['root'] != str(root) or (reference['root_dev'], reference['root_ino']) != identity
                or marker['state'] not in ('claimed', 'quiescent')
                or (marker['state'] == 'claimed' and marker['proof_kind'] is not None)
                or (marker['state'] == 'quiescent' and marker['proof_kind'] not in PROOFS)):
            raise ValueError()
        return marker
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise JobStateError('native_job_reference_invalid') from None


def _publish_marker(directory, reference, state, proof_kind):
    data = (_json({**reference, 'state': state, 'proof_kind': proof_kind}) + '\n').encode('ascii')
    if len(data) > MAX_MARKER_BYTES:
        raise JobStateError('native_job_reference_invalid')
    name = MARKER + '-' + uuid.uuid4().hex
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, MARKER, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass


def _marker_reference(marker):
    return {key: marker[key] for key in _REF_FIELDS}


def _row(connection, reference):
    row = connection.execute('SELECT reference,state,proof_kind FROM native_jobs WHERE job_id=?',
                             (reference['job_id'],)).fetchone()
    if (not row or row[0] != _json(reference) or row[1] not in ('claimed', 'quiescent')
            or (row[1] == 'claimed' and row[2] is not None)
            or (row[1] == 'quiescent' and row[2] not in PROOFS)):
        raise JobStateError('native_job_reference_invalid')
    return row[1], row[2]


def validate_reference(reference, root=None):
    reference = _reference_shape(reference)
    with _root(reference['root'] if root is None else root) as (path, _, identity):
        if reference['root'] != str(path) or (reference['root_dev'], reference['root_ino']) != identity:
            raise JobStateError('native_job_reference_invalid')
        if str(_database_path(reference['database'], path)) != reference['database']:
            raise JobStateError('native_job_reference_invalid')
        with _connection(reference['database'], path, readonly=True) as connection:
            _row(connection, reference)
    return reference


def claim_job(root, database, task_id, turn_id, operation_id, generation, intent):
    with _root(root) as (path, directory, identity), _marker_lock(directory):
        if not str(path).isascii():
            raise JobStateError('native_job_root_unsupported')
        database = str(_database_path(database, path))
        reference = {'version': 1, 'job_id': uuid.uuid4().hex, 'root': str(path), 'database': database,
                     'task_id': task_id, 'turn_id': turn_id, 'operation_id': operation_id, 'generation': generation,
                     'intent_sha256': hashlib.sha256(_json(intent).encode('ascii')).hexdigest(),
                     'root_dev': identity[0], 'root_ino': identity[1], 'backend': BACKEND}
        _reference_shape(reference)
        marker = _read_marker(directory, path, identity)
        with _connection(database, path, readonly=False) as connection:
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                row = connection.execute('SELECT state FROM tasks WHERE id=?', (task_id,)).fetchone()
                source = json.loads(row[0]) if row else {}
                pending = source.get('pending') or {}
                if (source.get('id') != task_id or source.get('turn_id') != turn_id or source.get('workspace') != str(path)
                        or source.get('phase') != 'tool_pending' or pending.get('status') != 'running'
                        or pending.get('operation_id') != operation_id or _json(pending.get('intent')) != _json(intent)
                        or not isinstance(intent, dict) or intent.get('name') not in ('run_tests', 'run_command', 'git_status', 'git_diff')):
                    raise JobStateError('native_job_source_mismatch')
                descriptor = os.open('.agent-lab.generation', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    token = os.read(descriptor, 65)
                finally:
                    os.close(descriptor)
                if token != (generation + '\n').encode('ascii'):
                    raise JobStateError('native_job_source_mismatch')
                connection.execute("CREATE TABLE IF NOT EXISTS native_jobs (job_id TEXT PRIMARY KEY,claim_key TEXT NOT NULL UNIQUE,reference TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('claimed','quiescent')),proof_kind TEXT,worker_token TEXT)")
                claim_key = hashlib.sha256(_json({key: value for key, value in reference.items() if key != 'job_id'}).encode('ascii')).hexdigest()
                old = connection.execute('SELECT reference,state,proof_kind FROM native_jobs WHERE claim_key=?', (claim_key,)).fetchone()
                if old:
                    reference = _reference_shape(json.loads(old[0]))
                    state, proof = _row(connection, reference)
                else:
                    state, proof = 'claimed', None
                if marker and marker['state'] == 'claimed' and _marker_reference(marker) != reference:
                    raise JobBlocked()
                if not old:
                    connection.execute('INSERT INTO native_jobs VALUES(?,?,?,?,?,NULL)',
                                        (reference['job_id'], claim_key, _json(reference), state, proof))
            _publish_marker(directory, reference, state, proof)
        return reference


def _token_shape(token):
    return isinstance(token, str) and re.fullmatch('[0-9a-f]{32}', token) is not None


def register_worker(reference, worker_token):
    if not _token_shape(worker_token):
        raise JobStateError('native_job_reference_invalid')
    reference = validate_reference(reference)
    with _root(reference['root']) as (path, directory, identity), _marker_lock(directory):
        marker = _read_marker(directory, path, identity)
        if not marker or marker['state'] != 'claimed' or _marker_reference(marker) != reference:
            raise JobBlocked()
        with _connection(reference['database'], path, readonly=False) as connection:
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                state, _ = _row(connection, reference)
                token = connection.execute('SELECT worker_token FROM native_jobs WHERE job_id=?',
                                           (reference['job_id'],)).fetchone()[0]
                if state != 'claimed' or token not in (None, worker_token):
                    raise JobStateError('native_job_reference_invalid')
                connection.execute('UPDATE native_jobs SET worker_token=? WHERE job_id=?',
                                   (worker_token, reference['job_id']))


def confirm_job(reference, proof_kind, worker_token=None):
    if proof_kind not in PROOFS:
        raise JobStateError('native_job_proof_invalid')
    reference = validate_reference(reference)
    with _root(reference['root']) as (path, directory, identity), _marker_lock(directory):
        marker = _read_marker(directory, path, identity)
        with _connection(reference['database'], path, readonly=False) as connection:
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                state, old_proof = _row(connection, reference)
                token = connection.execute('SELECT worker_token FROM native_jobs WHERE job_id=?',
                                           (reference['job_id'],)).fetchone()[0]
                if proof_kind == 'worker_launch_failed':
                    valid_producer = token is None and worker_token is None
                else:
                    valid_producer = _token_shape(worker_token) and token == worker_token
                if not valid_producer:
                    raise JobStateError('native_job_proof_invalid')
                if state == 'quiescent':
                    if old_proof != proof_kind:
                        raise JobStateError('native_job_proof_invalid')
                else:
                    if not marker or _marker_reference(marker) != reference:
                        raise JobBlocked()
                    connection.execute("UPDATE native_jobs SET state='quiescent',proof_kind=? WHERE job_id=? AND state='claimed'",
                                        (proof_kind, reference['job_id']))
            if marker and _marker_reference(marker) == reference:
                _publish_marker(directory, reference, 'quiescent', proof_kind)
        return reference


def require_quiescent(reference):
    reference = validate_reference(reference)
    with _connection(reference['database'], Path(reference['root']), readonly=True) as connection:
        if _row(connection, reference)[0] != 'quiescent':
            raise JobBlocked()


def ensure_workspace_idle(root, database=None):
    try:
        with _root(root) as (path, directory, identity):
            marker = _read_marker(directory, path, identity)
            if marker is None or marker['state'] == 'quiescent':
                return
            if database is not None and str(_database_path(database, path)) == marker['database']:
                # Initial readonly observation avoids creating a new control file
                # on old/no-job paths. Recheck under the publication lock; a newer
                # claim may have replaced the marker before we acquired it.
                with _marker_lock(directory):
                    marker = _read_marker(directory, path, identity)
                    if marker is None or marker['state'] == 'quiescent':
                        return
                    if str(_database_path(database, path)) == marker['database']:
                        reference = _marker_reference(marker)
                        with _connection(database, path, readonly=True) as connection:
                            state, proof = _row(connection, reference)
                        if state == 'quiescent':
                            _publish_marker(directory, reference, state, proof)
                            return
            raise JobBlocked()
    except (JobStateError, OSError, sqlite3.Error):
        raise JobBlocked() from None


def ensure_worker_allowed(root, reference=None):
    try:
        with _root(root) as (path, directory, identity):
            marker = _read_marker(directory, path, identity)
            if marker is None:
                if reference is not None:
                    raise JobBlocked()
                return
            if reference is None:
                if marker['state'] != 'quiescent':
                    raise JobBlocked()
                return
            reference = _reference_shape(reference)
            if marker['state'] != 'claimed' or _marker_reference(marker) != reference:
                raise JobBlocked()
            validate_reference(reference, path)
            with _connection(reference['database'], path, readonly=True) as connection:
                if _row(connection, reference)[0] != 'claimed':
                    raise JobBlocked()
    except (JobStateError, OSError, sqlite3.Error):
        raise JobBlocked() from None


def inspect_workspace(root, database=None):
    try:
        with _root(root) as (path, directory, identity):
            marker = _read_marker(directory, path, identity)
            if marker is None:
                return {'state': 'none', 'workspace_quarantined': False, 'database_checked': False,
                        'process_liveness': 'not_checked', 'filesystem_effects': 'not_checked'}
            report = {key: value for key, value in marker.items() if key not in ('root', 'database')}
            report.update(marker_state=marker['state'], database_checked=False,
                          process_liveness='not_checked', filesystem_effects='not_checked')
            if database is not None and str(_database_path(database, path)) == marker['database']:
                with _connection(database, path, readonly=True) as connection:
                    state, proof = _row(connection, _marker_reference(marker))
                report.update(state=state, proof_kind=proof, database_checked=True,
                              refresh_needed=state != marker['state'])
            report['workspace_quarantined'] = report['state'] != 'quiescent'
            return report
    except (JobStateError, OSError, sqlite3.Error, ValueError, TypeError):
        return {'state': 'unavailable', 'workspace_quarantined': True, 'database_checked': False,
                'error': 'workspace_quarantined', 'process_liveness': 'not_checked', 'filesystem_effects': 'not_checked'}
