"""Private candidate collector, deliberately not connected to production tools.

Caller must enforce fork group confinement before any guest code runs. Only the
exclusively owned zombie leader may remain before wait releases its identity.
This is a local lifecycle predicate, not an external-effect/OS-wide guarantee.
"""
import math
import os
import selectors
import signal
import subprocess
import time

from .native_job_runtime import current_tracker
from .process_group import GroupObservationError, OwnedGroup
from .tool_control import NativeCancelled, owner_dead
from .tool_output import emit_output
from .tools import ToolError

MAX_OUTPUT_BYTES = 65536


def _limits(timeout, cleanup_timeout, output_limit):
    try:
        valid = (type(timeout) in (int, float) and timeout > 0 and math.isfinite(timeout)
                 and type(cleanup_timeout) in (int, float) and 0 < cleanup_timeout <= 5
                 and math.isfinite(cleanup_timeout) and type(output_limit) is int
                 and 1 <= output_limit <= MAX_OUTPUT_BYTES)
    except OverflowError:
        valid = False
    if not valid:
        raise ToolError('invalid_collector_limits')


class _Pipes:
    def __init__(self, limit, stream):
        self.selector = selectors.DefaultSelector()
        self.limit, self.stream = limit, stream
        self.data = {name: bytearray() for name in ('stdout', 'stderr')}
        self.truncated = dict.fromkeys(self.data, False)

    def register(self, proc):
        for name in self.data:
            pipe = getattr(proc, name)
            os.set_blocking(pipe.fileno(), False)
            self.selector.register(pipe, selectors.EVENT_READ, name)

    def read(self, wait):
        if not self.selector.get_map():
            time.sleep(wait)
            return
        for key, _ in self.selector.select(wait):
            try:
                chunk = os.read(key.fd, 8192)
            except BlockingIOError:
                continue
            if not chunk:
                self.selector.unregister(key.fileobj)
                continue
            name = key.data
            capacity = self.limit - len(self.data[name])
            self.data[name].extend(chunk[:capacity])
            self.truncated[name] |= len(chunk) > capacity
            if self.stream:
                emit_output(name, chunk)

    def result(self, code):
        result = {'ok': code == 0, 'exit_code': code}
        for name, data in self.data.items():
            encoded = data.decode('utf-8', errors='replace').encode('utf-8')
            result[name] = encoded[:self.limit].decode('utf-8', errors='ignore')
            result[name+'_truncated'] = self.truncated[name] or len(encoded) > self.limit
        return result


def _join(proc, group, pipes, cleanup_timeout):
    """Retain anchor throughout drain. Failure cannot produce a join receipt."""
    deadline = time.monotonic() + cleanup_timeout
    io_failed = False
    while time.monotonic() < deadline:
        try:
            quiet = group.anchor_only_zombie()
        except GroupObservationError:
            quiet = False
        if quiet:
            # No signal/group query after this transition, including failed wait.
            group.mark_reap_started()
            try:
                code = proc.wait(timeout=max(0, deadline - time.monotonic()))
            except (OSError, subprocess.TimeoutExpired):
                raise ToolError('native_group_unconfirmed') from None
            tracker = current_tracker()
            if tracker is not None:
                tracker.native_joined(proc.pid)
            # Reaping does not itself drain buffered pipe data. The pipe deadline
            # is bounded too; its failure cannot revoke a valid lifecycle join.
            while pipes.selector.get_map() and not io_failed and time.monotonic() < deadline:
                try:
                    pipes.read(min(.01, max(0, deadline - time.monotonic())))
                except (OSError, ValueError):
                    io_failed = True
            if io_failed or pipes.selector.get_map():
                raise ToolError('tool_output_incomplete')
            return code
        try:
            # Even failed enumeration may still have a valid anchor. Never use
            # cached PGIDs or kill individual snapshot PIDs after identity loss.
            os.killpg(group.signal_target(), signal.SIGKILL)
        except (GroupObservationError, OSError):
            pass
        if io_failed:
            time.sleep(min(.01, max(0, deadline - time.monotonic())))
        else:
            try:
                pipes.read(min(.01, max(0, deadline - time.monotonic())))
            except (OSError, ValueError):
                io_failed = True
    raise ToolError('native_group_unconfirmed')


def collect(argv, root, env, timeout, *, output_limit=MAX_OUTPUT_BYTES,
            cleanup_timeout=1., stream_output=False):
    """Candidate only. No guest control FDs, no poll/communicate before drain.

    Unknown cleanup keeps the supervisor tracker populated even if the owned
    leader can later be reaped. Its descendants have not been attested joined.
    """
    _limits(timeout, cleanup_timeout, output_limit)
    if owner_dead():
        raise NativeCancelled('tool_cancelled')
    tracker = current_tracker()
    pipes = _Pipes(output_limit, stream_output)
    proc = group = None
    deadline = time.monotonic() + timeout
    try:
        if tracker is not None:
            tracker.native_begin()
        try:
            proc = subprocess.Popen(argv, cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    start_new_session=True, close_fds=True)
        except OSError:
            if tracker is not None:
                tracker.native_failed()
            raise ToolError('sandbox_unavailable') from None
        if tracker is not None:
            tracker.native_started(proc.pid)
        try:
            group = OwnedGroup(proc)
        except GroupObservationError:
            raise ToolError('native_group_unconfirmed') from None
        error = None
        try:
            pipes.register(proc)
            while True:
                if owner_dead():
                    raise NativeCancelled('tool_cancelled')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ToolError('tool_timeout')
                pipes.read(min(.02, remaining))
                if group.anchor().status == 5:
                    break
        except BaseException as caught:
            error = caught
            # A failing display observer must not prevent lifecycle cleanup.
            pipes.stream = False
        code = _join(proc, group, pipes, cleanup_timeout)
        if error is not None:
            raise error
        return pipes.result(code)
    finally:
        if proc is not None:
            # Only a nonblocking best effort reap after a failed protocol. No
            # group access follows this, and it never clears the native tracker.
            if proc.returncode is None:
                if group is not None:
                    group.mark_reap_started()
                try:
                    proc.wait(timeout=0)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            proc.stdout.close()
            proc.stderr.close()
        pipes.selector.close()
