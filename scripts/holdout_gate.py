"""Private one-attempt admission after possible provider exposure, not delivery proof."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

MAX_MARKER_BYTES = 1024


class ExposureError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ExposureBlocked(ExposureError):
    def __init__(self):
        super().__init__('holdout_exposure_blocked')


class ExposureUnavailable(ExposureError):
    def __init__(self):
        super().__init__('holdout_exposure_unavailable')


def _private_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise ExposureUnavailable()


def _identity(info):
    return info.st_dev, info.st_ino


class ExposureGate:
    """Hold a private case lock and durably mark before each first delegation.

    No marker is removed on exit. A crash between mark and provider access still
    consumes this gate: the record asserts possibility, never actual delivery.
    """
    def __init__(self, root, suite_sha, case_id, run_id):
        if (not isinstance(root, (str, Path)) or not str(root) or '\x00' in str(root)
                or not isinstance(suite_sha, str) or re.fullmatch('[0-9a-f]{64}', suite_sha) is None
                or any(not isinstance(value, str) or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value) is None
                       for value in (case_id, run_id))):
            raise ValueError('invalid_holdout_exposure_gate')
        self.root = Path(root).absolute()
        key = hashlib.sha256((suite_sha + case_id).encode('ascii')).hexdigest()
        self._lock_name, self._marker_name = key + '.lock', key + '.exposure.json'
        self._metadata = (json.dumps({'version': 1, 'suite_sha': suite_sha, 'case_id': case_id,
                                     'run_id': run_id, 'state': 'possible_provider_exposure'},
                                    sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')
        if len(self._metadata) > MAX_MARKER_BYTES:
            raise ValueError('invalid_holdout_exposure_gate')
        self._state = 'new'
        self._directory = self._lock = None
        self._durable = self._poisoned = False
        self._possible = False

    @property
    def exposure_possible(self):
        return self._possible

    def _close(self):
        for name in ('_lock', '_directory'):
            descriptor = getattr(self, name)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                setattr(self, name, None)

    def __enter__(self):
        if self._state != 'new':
            raise ExposureBlocked()
        self._state = 'failed'
        try:
            if self.root.is_symlink():
                raise ExposureUnavailable()
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.root = self.root.resolve()
            self._directory = os.open(str(self.root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(self._directory)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ExposureUnavailable()
            self._root_identity = _identity(info)
            self._lock = os.open(self._lock_name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600, dir_fd=self._directory)
            _private_file(os.fstat(self._lock))
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ExposureBlocked() from None
            self._lock_identity = _identity(os.fstat(self._lock))
            try:
                os.stat(self._marker_name, dir_fd=self._directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                self._possible = True
                raise ExposureBlocked()
            self._pid = os.getpid()
            self._state = 'active'
            return self
        except ExposureError:
            self._close()
            raise
        except (OSError, ValueError, RuntimeError):
            self._close()
            raise ExposureUnavailable() from None

    def _check_active(self):
        if self._state != 'active' or os.getpid() != self._pid:
            raise ExposureBlocked()
        try:
            root = os.stat(self.root, follow_symlinks=False)
            lock = os.stat(self._lock_name, dir_fd=self._directory, follow_symlinks=False)
            _private_file(lock)
            _private_file(os.fstat(self._lock))
            if (_identity(root) != self._root_identity or _identity(lock) != self._lock_identity
                    or _identity(os.fstat(self._lock)) != self._lock_identity):
                raise ExposureUnavailable()
        except OSError:
            raise ExposureUnavailable() from None

    def _verify_marker(self):
        descriptor = os.open(self._marker_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=self._directory)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            _private_file(before)
            raw = stream.read(MAX_MARKER_BYTES + 1)
            after = os.fstat(stream.fileno())
            if (_identity(before) != self._marker_identity or raw != self._metadata
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ExposureUnavailable()

    def mark_exposure(self):
        self._check_active()
        if self._poisoned:
            raise ExposureUnavailable()
        if self._durable:
            try:
                self._verify_marker()
            except (ExposureError, OSError):
                self._poisoned = True
                raise ExposureUnavailable() from None
            return
        self._poisoned = True
        descriptor = None
        try:
            descriptor = os.open(self._marker_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600, dir_fd=self._directory)
            self._possible = True  # Even a partial or not-yet-fsynced marker counts.
            os.fchmod(descriptor, 0o600)
            info = os.fstat(descriptor)
            _private_file(info)
            self._marker_identity = _identity(info)
            offset = 0
            while offset < len(self._metadata):
                written = os.write(descriptor, self._metadata[offset:])
                if written <= 0:
                    raise OSError()
                offset += written
            os.fsync(descriptor)
            os.fsync(self._directory)
            self._check_active()
            self._durable = True
            self._poisoned = False
        except FileExistsError:
            self._possible = True
            raise ExposureBlocked() from None
        except (ExposureError, OSError):
            raise ExposureUnavailable() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def __exit__(self, *exc):
        self._state = 'closed'
        self._close()


class ExposureModel:
    """Transparent provider properties; mark before accessing provider complete."""
    def __init__(self, delegate, gate):
        if not isinstance(gate, ExposureGate):
            raise ValueError('invalid_holdout_exposure_gate')
        self.delegate, self.gate = delegate, gate

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def complete(self, *args, **kwargs):
        self.gate.mark_exposure()
        return self.delegate.complete(*args, **kwargs)
