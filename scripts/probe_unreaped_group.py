"""NoFork, one owned child: observe NOTE_EXIT/zombie/group before explicit reap.

Research only. No generic PID inputs, command-line enumeration or quiescence
receipt. proc_listpgrppids wraps errors as0; return errno and never infer quiet.
"""
import ctypes
import hashlib
import errno
import json
import os
from pathlib import Path
import select
import selectors
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_lab import sandbox
from agent_lab.process_group import OwnedGroup,GroupObservationError


class BSDInfo(ctypes.Structure):
    _fields_=[(name,ctypes.c_uint32) for name in (
        'flags','status','xstatus','pid','ppid','uid','gid','ruid','rgid','svuid','svgid','reserved')]+[
        ('comm',ctypes.c_char*16),('name',ctypes.c_char*32)]+[
        (name,ctypes.c_uint32) for name in ('nfiles','pgid','pjobc','tdev','tpgid')]+[
        ('nice',ctypes.c_int32),('start_sec',ctypes.c_uint64),('start_usec',ctypes.c_uint64)]


def library():
    lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
    lib.proc_pidinfo.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int]
    lib.proc_pidinfo.restype=ctypes.c_int
    lib.proc_listpgrppids.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_int]
    lib.proc_listpgrppids.restype=ctypes.c_int
    return lib


def observation(lib,pid,*,zombies):
    info=BSDInfo();ctypes.set_errno(0)
    size=lib.proc_pidinfo(pid,3,int(zombies),ctypes.byref(info),ctypes.sizeof(info))
    error=ctypes.get_errno()
    return {'returned_bytes':size,'errno':error,'status':info.status if size==ctypes.sizeof(info) else None,
            'owned_pid_matches':bool(info.pid==pid) if size==ctypes.sizeof(info) else None,
            'group_matches_owned_leader':bool(info.pgid==pid) if size==ctypes.sizeof(info) else None}


def group(lib,pid):
    pids=(ctypes.c_int*16)();ctypes.set_errno(0)
    count=lib.proc_listpgrppids(pid,pids,ctypes.sizeof(pids));error=ctypes.get_errno()
    return {'reported_count':count,'errno':error,'buffer_full':count>=len(pids),
            'nonzero_slots':sum(p>0 for p in pids),'contains_owned_leader':pid in pids,
            'only_owned_leader':count==1 and pids[0]==pid}


def ready(proc):
    data=bytearray();deadline=time.monotonic()+3
    os.set_blocking(proc.stdout.fileno(),False)
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout,selectors.EVENT_READ)
        while time.monotonic()<deadline:
            for key,_ in selector.select(max(0,deadline-time.monotonic())):
                chunk=os.read(key.fd,128)
                if not chunk:raise RuntimeError('owned_child_not_ready')
                data.extend(chunk)
                if data==b'ready\n':return
                if len(data)>128:raise RuntimeError('owned_child_not_ready')
    raise RuntimeError('owned_child_not_ready')


def probe():
    lib=library();native=Path(sys.base_prefix)/'Resources/Python.app/Contents/MacOS/Python'
    if ctypes.sizeof(BSDInfo)!=136:raise RuntimeError('unsupported_bsdinfo_abi')
    with tempfile.TemporaryDirectory(prefix='agentlab-unreaped-',dir='/private/tmp') as directory:
        base=Path(directory);root=base/'work';root.mkdir();scratch=base/'scratch';scratch.mkdir()
        profile=sandbox._profile(root,scratch)+'\n(deny syscall-unix (syscall-number 82 147 244))'
        env={'PATH':os.defpath,'LANG':'C.UTF-8','HOME':str(scratch),'TMPDIR':str(scratch),
             'PYTHONDONTWRITEBYTECODE':'1'}
        body="import os;print('ready',flush=True);os.read(0,1);os._exit(7)"
        proc=subprocess.Popen([sandbox.BACKEND_PATH,'-p',profile,str(native),'-I','-c',body],
            cwd=root,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            start_new_session=True,close_fds=True,bufsize=0)
        events=select.kqueue()
        observer=None
        try:
            ready(proc)
            observer=OwnedGroup(proc)
            events.control([select.kevent(proc.pid,filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD|select.KQ_EV_ENABLE|select.KQ_EV_ONESHOT,
                fflags=select.KQ_NOTE_EXIT)],0,0)
            running={'bsd':observation(lib,proc.pid,zombies=False),'group':group(lib,proc.pid)}
            proc.stdin.write(b'x');proc.stdin.flush()
            notice=events.control(None,1,3)
            if not notice or not notice[0].fflags&select.KQ_NOTE_EXIT:raise RuntimeError('owned_exit_notice_missing')
            unreaped={'popen_returncode_still_unset':proc.returncode is None,
                'bsd_without_zombies':observation(lib,proc.pid,zombies=False),
                'bsd_with_zombies':observation(lib,proc.pid,zombies=True),'group':group(lib,proc.pid)}
            unreaped['observer_singleton_zombie']=observer.anchor_only_zombie()
            observer.mark_reap_started()
            code=proc.wait(timeout=2)
            reaped={'exit_code':code,'bsd_with_zombies':observation(lib,proc.pid,zombies=True),
                    'group':group(lib,proc.pid)}
            try:observer.signal_target();raise RuntimeError('observer_released_target_accepted')
            except GroupObservationError:reaped['observer_rejects_released_target']=True
            return {'kind':'owned_nofork_unreaped_group_observation','bsdinfo_abi_bytes':ctypes.sizeof(BSDInfo),
                'observer_source_sha256':hashlib.sha256((Path(__file__).resolve().parents[1]/'agent_lab'/'process_group.py').read_bytes()).hexdigest(),
                'running':running,'exit_event_observed':True,'unreaped':unreaped,'reaped':reaped,
                'production_policy_changed':False,'fork_enabled':False,'quiescence_proven':False,
                'provider_requests':0,'credential_file_read':False,'real_budget_opened':False}
        finally:
            # NoFork child identity stays owned until this one wait. No raw PGID
            # signals or unrelated process enumeration occur in this first gate.
            if proc.returncode is None:
                try:os.kill(proc.pid,signal.SIGKILL)
                except (ProcessLookupError,PermissionError):pass
                if observer is not None:observer.mark_reap_started()
                proc.wait(timeout=2)
            events.close()
            for stream in (proc.stdin,proc.stdout,proc.stderr):
                try:stream.close()
                except OSError:pass


if __name__=='__main__':print(json.dumps(probe(),indent=2))
