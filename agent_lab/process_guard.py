"""Cooperative POSIX worker fencing, independent of the runner's lock.

A surviving worker owns the execution lease itself. A replacement runner must
hold its normal runner lock before rotating, and cannot rotate while a worker
holds this lease. Workers acquire the lease *before* comparing their generation
and retain it across the complete effect. No parent-death cleanup is assumed.

The control files must be protected from project code by the tool/sandbox layer;
this is not a lock against an actor allowed to unlink the lease file.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import os
from pathlib import Path
import re
import stat
from typing import Optional
import uuid


_GENERATION = '.agent-lab.generation'
_LEASE = '.agent-lab.execution.lock'
_ACTIVE_LEASE = ContextVar('agent_lab_active_execution_lease', default=None)


class ExecutionBusy(Exception):
    """A worker or generation rotation already owns the execution lease."""


class StaleGeneration(Exception):
    """A worker belongs to an earlier runner generation."""


@contextmanager
def _root_directory(root):
    # Pin the directory so subsequent operations cannot follow a substituted
    # final root symlink. Callers supply their already-selected workspace root.
    # Path strips a trailing slash, which would otherwise defeat O_NOFOLLOW for
    # a final directory symlink on POSIX systems.
    fd = os.open(os.fspath(Path(root)), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def _open_control(directory, name, flags):
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
                 dir_fd=directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('unsafe_process_control_file')
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _execution_lease(directory):
    fd = _open_control(directory, _LEASE, os.O_CREAT | os.O_RDWR)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExecutionBusy('execution_busy') from None
        yield fd
    finally:
        # flock belongs to this open file description. Closing our reference
        # releases it only after any trusted inheritor has also closed theirs;
        # explicit LOCK_UN would prematurely release their shared lease.
        os.close(fd)


def _read_generation(directory):
    try:
        fd = _open_control(directory, _GENERATION, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        raw = os.read(fd, 65)
    finally:
        os.close(fd)
    if re.fullmatch(rb'[0-9a-f]{32}\n', raw) is None:
        raise ValueError('invalid_process_generation')
    return raw[:-1].decode('ascii')


def current_generation(root) -> Optional[str]:
    """Read a complete published token, or None before the first rotation."""
    with _root_directory(root) as directory:
        return _read_generation(directory)


def active_lease_fd() -> Optional[int]:
    """Return this context's lease for explicit trusted supervisor inheritance.

    The descriptor remains non-inheritable by default. Trusted callers may use
    pass_fds when launching their supervisor; never expose it to project code
    or accept a descriptor selected through model arguments. The supervisor
    must close it only after its effectful descendants have stopped.
    """
    return _ACTIVE_LEASE.get()


def rotate_generation(root) -> str:
    """Publish a new token while idle; caller must hold the runner lock.

    Raises ExecutionBusy immediately if an old worker is still executing. Never
    unlink or replace the execution lease: its inode coordinates all workers.
    """
    with _root_directory(root) as directory, _execution_lease(directory):
        try:
            from .native_jobs import ensure_workspace_idle
        except ImportError:
            from native_jobs import ensure_workspace_idle
        ensure_workspace_idle(root)
        # Validate an existing control path before replacing it, including
        # rejecting symlinks, hard links, and non-regular files.
        _read_generation(directory)
        token = uuid.uuid4().hex
        temporary = '.agent-lab.generation-' + uuid.uuid4().hex
        fd = _open_control(directory, temporary,
                           os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write((token + '\n').encode('ascii'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, _GENERATION,
                       src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        return token


@contextmanager
def worker_guard(root, expected: Optional[str], *, native_job_reference=None):
    """Fence a complete worker effect against rotation and other workers.

    None is compatible with direct tool use only before any runner generation
    exists. This context must be entered by the executing worker, not its parent.
    """
    with _root_directory(root) as directory, _execution_lease(directory) as fd:
        try:
            from .native_jobs import ensure_worker_allowed
        except ImportError:
            from native_jobs import ensure_worker_allowed
        ensure_worker_allowed(root, native_job_reference)
        if _read_generation(directory) != expected:
            raise StaleGeneration('stale_generation')
        context_token = _ACTIVE_LEASE.set(fd)
        try:
            yield fd
        finally:
            _ACTIVE_LEASE.reset(context_token)
