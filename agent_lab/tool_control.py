"""Host-only cancellation scope and ownership for trusted native tool workers."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import select
import stat


class ToolCancelled(Exception):
    """Only parent-side validated supervisor cleanup can attest quiescence."""
    def __init__(self, local_quiescent=True):
        self.code = 'tool_cancelled'
        self.local_quiescent = local_quiescent is True
        super().__init__(self.code)


def cancelled_receipt(value):
    return (isinstance(value, dict) and set(value) == {'cancelled', 'local_quiescent'}
            and value['cancelled'] is True and value['local_quiescent'] is True)


class NativeCancelled(Exception):
    """Internal trusted-collector signal, emitted only after its cleanup."""
    pass


class CheckFailed(Exception):
    pass


class CancellationState:
    def __init__(self):
        self.checks = ContextVar('tool_cancel_' + str(id(self)), default=())

    @contextmanager
    def scope(self, check):
        if not callable(check):
            raise ValueError('invalid_cancel_check')
        token = self.checks.set(self.checks.get() + (check,))
        try:
            yield
        finally:
            self.checks.reset(token)

    def requested(self):
        for check in self.checks.get():
            try:
                if check():
                    return True
            except Exception:
                raise CheckFailed('tool_cancel_check_failed') from None
        return False


_OWNER = ContextVar('native_tool_owner', default=None)
OWNER_ENV = 'AGENT_LAB_TOOL_OWNER_FD'


def owner_read_fd():
    return _OWNER.get()


@contextmanager
def inherited_owner():
    value = os.environ.pop(OWNER_ENV, None)
    descriptor = None
    if value is not None:
        try:
            descriptor = int(value)
            if not stat.S_ISFIFO(os.fstat(descriptor).st_mode):
                raise ValueError()
            os.set_inheritable(descriptor, False)
        except (ValueError, OSError):
            raise ValueError('invalid_tool_owner') from None
    token = _OWNER.set(descriptor)
    try:
        yield
    finally:
        _OWNER.reset(token)
        if descriptor is not None:
            os.close(descriptor)


def owner_dead():
    descriptor = owner_read_fd()
    if descriptor is None:
        return False
    return bool(select.select([descriptor], [], [], 0)[0]) and os.read(descriptor, 1) == b''


class OwnerPipe:
    """Runner owns the only writer; trusted worker/supervisor inherit read only."""
    def __enter__(self):
        if os.name != 'posix':
            raise OSError('tool_owner_unsupported')
        self.read, self.write = os.pipe()
        return self

    def cancel(self):
        if self.write is not None:
            os.close(self.write)
            self.write = None

    def __exit__(self, *exc):
        try:
            self.cancel()
        finally:
            os.close(self.read)
