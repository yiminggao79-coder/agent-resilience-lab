"""Read-only macOS observation of a trusted parent's unreaped group leader.

Not used by the production NoFork collector yet. The narrow singleton-zombie
predicate is not a universal process-tree quiescence or effect receipt. The caller
must exclusively retain wait ownership and separately enforce group confinement.
"""
import ctypes
from dataclasses import dataclass
import os
import platform
import signal


class GroupObservationError(Exception):
    def __init__(self,code='group_observation_unavailable'):
        self.code=code;super().__init__(code)


@dataclass(frozen=True)
class ProcessInfo:
    pid:int
    ppid:int
    pgid:int
    status:int
    start_sec:int
    start_usec:int


class _BSDInfo(ctypes.Structure):
    # Host SDK sys/proc_info.h proc_bsdinfo: fixed 64-bit macOS ABI, 136 bytes.
    _fields_=[(name,ctypes.c_uint32) for name in (
        'flags','status','xstatus','pid','ppid','uid','gid','ruid','rgid','svuid','svgid','reserved')]+[
        ('comm',ctypes.c_char*16),('name',ctypes.c_char*32)]+[
        (name,ctypes.c_uint32) for name in ('nfiles','pgid','pjobc','tdev','tpgid')]+[
        ('nice',ctypes.c_int32),('start_sec',ctypes.c_uint64),('start_usec',ctypes.c_uint64)]


class _Sigaction(ctypes.Structure):
    # Public libc sigaction, not the kernel's __sigaction with sa_tramp.
    _fields_=[('handler',ctypes.c_void_p),('mask',ctypes.c_uint32),('flags',ctypes.c_int)]


class DarwinProcesses:
    """Bounded libproc calls. Failure-as-zero and saturation are not empty proof."""
    def __init__(self):
        if platform.system()!='Darwin' or ctypes.sizeof(_BSDInfo)!=136 or ctypes.sizeof(_Sigaction)!=16:
            raise GroupObservationError('group_backend_unavailable')
        try:
            self._lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
            self._lib.proc_pidinfo.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int]
            self._lib.proc_pidinfo.restype=ctypes.c_int
            self._lib.proc_listpgrppids.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_int]
            self._lib.proc_listpgrppids.restype=ctypes.c_int
            self._libc=ctypes.CDLL(None,use_errno=True)
            self._libc.sigaction.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p]
            self._libc.sigaction.restype=ctypes.c_int
        except (OSError,AttributeError):raise GroupObservationError('group_backend_unavailable') from None

    def wait_policy_ok(self):
        action=_Sigaction();ctypes.set_errno(0)
        result=self._libc.sigaction(signal.SIGCHLD,None,ctypes.byref(action))
        # SDK sys/signal.h: SA_NOCLDWAIT=0x20. Also reject a native or Python
        # handler that could reap while the caller believes its child is pinned.
        return (result==0 and ctypes.get_errno()==0 and action.handler is None
                and not action.flags&0x20 and signal.getsignal(signal.SIGCHLD)==signal.SIG_DFL)

    def process(self,pid):
        info=_BSDInfo();ctypes.set_errno(0)
        # BSD flavor3 requires arg!=0 to include exited/zombie objects.
        size=self._lib.proc_pidinfo(pid,3,1,ctypes.byref(info),ctypes.sizeof(info))
        if size!=ctypes.sizeof(info) or ctypes.get_errno()!=0:
            raise GroupObservationError()
        return ProcessInfo(info.pid,info.ppid,info.pgid,info.status,info.start_sec,info.start_usec)

    def members(self,pgid,capacity):
        pids=(ctypes.c_int*capacity)();ctypes.set_errno(0)
        count=self._lib.proc_listpgrppids(pgid,pids,ctypes.sizeof(pids))
        if ctypes.get_errno()!=0 or not 0<=count<=capacity:raise GroupObservationError()
        return tuple(pids[:count])


class OwnedGroup:
    """Never polls/reaps/signals; validates only an exclusively owned reference.

    No arbitrary PID CLI or model tool. Caller uses its retained Popen and must
    call mark_reap_started before any wait/poll/communicate or losing ownership.
    """
    def __init__(self,child,*,backend=None,initial_capacity=16,max_capacity=4096):
        if (type(initial_capacity) is not int or type(max_capacity) is not int
                or not 2<=initial_capacity<=max_capacity<=4096):
            raise ValueError('invalid_group_capacity')
        self._child=child;self._backend=backend if backend is not None else DarwinProcesses()
        self._owner=os.getpid();self._reap_started=False
        self._initial_capacity,self._max_capacity=initial_capacity,max_capacity
        pid=getattr(child,'pid',None)
        if type(pid) is not int or not 0<pid<2**31:raise GroupObservationError('group_anchor_lost')
        self.pid=pid
        initial=self._read_anchor()
        self._identity=(initial.pid,initial.ppid,initial.pgid,initial.start_sec,initial.start_usec)

    def _read_anchor(self):
        if (self._reap_started or self._owner!=os.getpid()
                or self._child.returncode is not None):raise GroupObservationError('group_anchor_lost')
        try:
            if self._backend.wait_policy_ok() is not True:raise GroupObservationError('group_wait_not_exclusive')
            info=self._backend.process(self.pid)
        except GroupObservationError:raise
        except (OSError,ValueError,TypeError):raise GroupObservationError() from None
        if (not isinstance(info,ProcessInfo)
                or any(type(v) is not int for v in (info.pid,info.ppid,info.pgid,info.status,info.start_sec,info.start_usec))
                or info.pid!=self.pid or info.ppid!=self._owner or info.pgid!=self.pid
                or not 1<=info.status<=5 or info.start_sec<=0 or not 0<=info.start_usec<1000000):
            raise GroupObservationError('group_anchor_lost')
        return info

    def anchor(self):
        info=self._read_anchor()
        if (info.pid,info.ppid,info.pgid,info.start_sec,info.start_usec)!=self._identity:
            raise GroupObservationError('group_anchor_lost')
        return info

    def members(self):
        capacity=self._initial_capacity
        while True:
            self.anchor()
            try:pids=self._backend.members(self.pid,capacity)
            except GroupObservationError:raise
            except (OSError,ValueError,TypeError):raise GroupObservationError() from None
            if (not isinstance(pids,(tuple,list)) or len(pids)>capacity
                    or any(type(pid) is not int or not 0<pid<2**31 for pid in pids)
                    or len(set(pids))!=len(pids) or self.pid not in pids):
                raise GroupObservationError()
            if len(pids)<capacity:
                self.anchor()
                return tuple(pids)
            if capacity==self._max_capacity:raise GroupObservationError('group_capacity_limit')
            capacity=min(capacity*2,self._max_capacity)

    def anchor_only_zombie(self):
        if self.anchor().status!=5:return False
        if self.members()!=(self.pid,):return False
        return self.anchor().status==5

    def signal_target(self):
        # Returns the owned PGID, not permission to signal arbitrary snapshots.
        # Caller must keep exclusive wait ownership across the immediate syscall.
        self.anchor();return self.pid

    def mark_reap_started(self):
        # Disable later group access even if wait is interrupted/fails. Never
        # signal a number after the owner has started releasing its identity.
        self._reap_started=True
