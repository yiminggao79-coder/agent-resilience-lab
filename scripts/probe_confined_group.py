"""Research-only bounded fork/exec fixture; never a production backend switch.

Fixed six-role maximum plus short fixed interpreter/pipeline children. Guest
control EOF is emergency cleanup for trusted fixture code only, not the proof.
"""
import json
import hashlib
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_lab import sandbox
from agent_lab.process_group import OwnedGroup,GroupObservationError
from scripts.probe_unreaped_group import ready

BODY=r'''
import json,os,socket,subprocess,sys,time
from pathlib import Path
control=int(sys.argv[1]);private=Path(sys.argv[2]);original_group=os.getpgrp()
os.set_blocking(control,False)
def active():
 try:return os.read(control,1)!=b''
 except BlockingIOError:return True
def actor(name,deadline):
 count=0
 while active() and time.monotonic()<deadline:
  count+=1;Path('late-'+name).write_text(str(count));time.sleep(.01)
 os._exit(0)
print('ready',flush=True)
if os.read(0,1)!=b'g':os._exit(9)
child=os.fork()
if child:
 deadline=time.monotonic()+2.5
 while not Path('descendant-ready.json').exists() and time.monotonic()<deadline:time.sleep(.002)
 os._exit(7 if Path('descendant-ready.json').exists() else 11)
for fd in (0,1,2):
 try:os.close(fd)
 except OSError:pass
if os.fork():os._exit(0)
observed={'inherited_group':os.getpgrp()==original_group,'not_group_leader':os.getpid()!=os.getpgrp()}
for label,operation in (
 ('setsid',lambda:os.setsid()),('setpgid',lambda:os.setpgid(0,0)),
 ('spawn',lambda:os.posix_spawn('/usr/bin/true',['true'],{},setpgroup=0))):
 try:
  value=operation();observed[label+'_errno']=None
  if label=='spawn':os.waitpid(value,0)
 except OSError as error:observed[label+'_errno']=error.errno
try:private.read_text();observed['private_denied']=False
except PermissionError:observed['private_denied']=True
try:socket.socket().connect(('127.0.0.1',9));observed['network_denied']=False
except PermissionError:observed['network_denied']=True
except OSError:observed['network_denied']=False
try:
 result=subprocess.run([sys.executable,'-I','-c','print("owned")'],capture_output=True,timeout=.5)
 observed['python_fork_exec_ok']=result.returncode==0 and result.stdout==b'owned\n'
except (OSError,subprocess.TimeoutExpired):observed['python_fork_exec_ok']=False
try:
 result=subprocess.run(['/bin/sh','-c','echo owned | cat'],capture_output=True,timeout=.5)
 observed['shell_pipeline_ok']=result.returncode==0 and result.stdout==b'owned\n'
except (OSError,subprocess.TimeoutExpired):observed['shell_pipeline_ok']=False
Path('descendant-ready.tmp').write_text(json.dumps(observed))
os.replace('descendant-ready.tmp','descendant-ready.json')
deadline=time.monotonic()+1.5
while active() and time.monotonic()<deadline and not Path('release-effects').exists():time.sleep(.002)
if not active() or time.monotonic()>=deadline:os._exit(0)
for index in range(2):
 if os.fork()==0:actor(str(index),deadline)
 time.sleep(.005)
actor('grandchild',deadline)
'''


def effects(root):
    return {p.name:p.read_bytes() for p in root.glob('late-*') if p.is_file()}


def probe():
    with tempfile.TemporaryDirectory(prefix='agentlab-confined-group-',dir='/private/tmp') as directory:
        base=Path(directory);root=base/'work';root.mkdir();scratch=base/'scratch';scratch.mkdir()
        private=base/'private.txt';private.write_text('owned sentinel, no user data')
        native=Path(sys.base_prefix)/'Resources/Python.app/Contents/MacOS/Python'
        profile=sandbox._command_profile(root,scratch)
        profile+='\n(allow process-fork)\n(deny syscall-unix (syscall-number 82 147 244))'
        reader,writer=os.pipe()
        proc=None;observer=None;events=None;confirmed=False;result={}
        started=time.monotonic()
        try:
            proc=subprocess.Popen([sandbox.BACKEND_PATH,'-p',profile,str(native),'-I','-c',BODY,str(reader),str(private)],
                cwd=root,env={'PATH':os.defpath,'LANG':'C.UTF-8','HOME':str(scratch),'TMPDIR':str(scratch),
                    'PYTHONDONTWRITEBYTECODE':'1'},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                start_new_session=True,close_fds=True,pass_fds=(reader,),bufsize=0)
            os.close(reader);reader=None
            ready(proc);observer=OwnedGroup(proc);events=select.kqueue()
            events.control([select.kevent(proc.pid,filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD|select.KQ_EV_ENABLE|select.KQ_EV_ONESHOT,fflags=select.KQ_NOTE_EXIT)],0,0)
            proc.stdin.write(b'g');proc.stdin.flush()
            notice=events.control(None,1,4)
            if not notice or not notice[0].fflags&select.KQ_NOTE_EXIT:raise RuntimeError('owned_exit_notice_missing')
            deadline=time.monotonic()+1
            while observer.anchor().status!=5:
                if time.monotonic()>=deadline:raise RuntimeError('owned_zombie_not_observed')
                time.sleep(.002)
            data=json.loads((root/'descendant-ready.json').read_text())
            if not (all(data[k]==1 for k in ('setsid_errno','setpgid_errno','spawn_errno'))
                    and all(data[k] is True for k in ('inherited_group','not_group_leader','private_denied','network_denied'))):
                raise RuntimeError('confinement_checks_failed')
            result['fixed_guest_observations']=data
            result['leader_exit_is_not_quiet']=not observer.anchor_only_zombie()
            result['pipe_eof_is_not_quiet']=os.read(proc.stdout.fileno(),128)==b'' and result['leader_exit_is_not_quiet']
            if not result['leader_exit_is_not_quiet']:raise RuntimeError('live_descendant_not_observed')
            (root/'release-effects').write_text('owned test release')
            deadline=time.monotonic()+.5
            while not any(effects(root).values()):
                if time.monotonic()>=deadline:raise RuntimeError('post_exit_effect_missing')
                time.sleep(.002)
            result['effect_after_leader_zombie']=True
            # The leader remains unreaped throughout. Group signals target that
            # pinned PGID only; no kill of individual snapshot PIDs.
            attempts=denied=0;maximum=len(observer.members());drain_start=time.monotonic();deadline=drain_start+1
            while not observer.anchor_only_zombie():
                if time.monotonic()>=deadline:raise RuntimeError('group_drain_deadline')
                maximum=max(maximum,len(observer.members()))
                try:os.killpg(observer.signal_target(),signal.SIGKILL)
                except PermissionError:denied+=1
                attempts+=1;time.sleep(.01)
            confirmed=True
            result.update({'anchor_only_before_reap':True,'group_kill_attempts':attempts,
                           'permission_denials_during_drain':denied,'max_observed_group_members':maximum,
                           'drain_elapsed_seconds':round(time.monotonic()-drain_start,6)})
            before=effects(root);time.sleep(.1)
            result['effects_stable_after_drain']=effects(root)==before
            observer.mark_reap_started();result['leader_exit_code']=proc.wait(timeout=2)
            try:observer.signal_target();raise RuntimeError('released_group_target_accepted')
            except GroupObservationError:result['released_group_access_refused']=True
            result['experiment_passed']=result['effects_stable_after_drain'] and result['leader_exit_code']==7
        finally:
            # Emergency EOF is fixed-fixture cooperation, never a production
            # receipt. It stops all trusted actors even if a research check fails.
            if writer is not None:os.close(writer)
            if reader is not None:os.close(reader)
            if proc is not None:
                if proc.returncode is None:
                    if observer is not None:
                        until=time.monotonic()+3
                        while time.monotonic()<until:
                            try:
                                if observer.anchor_only_zombie():break
                            except GroupObservationError:break
                            time.sleep(.01)
                        observer.mark_reap_started()
                    try:os.kill(proc.pid,signal.SIGKILL)
                    except (ProcessLookupError,PermissionError):pass
                    proc.wait(timeout=2)
                for stream in (proc.stdin,proc.stdout,proc.stderr):
                    try:stream.close()
                    except OSError:pass
            if events is not None:events.close()
        result.update({'kind':'research_owned_confined_fork_group','production_policy_changed':False,
            'observer_source_sha256':hashlib.sha256((Path(__file__).resolve().parents[1]/'agent_lab'/'process_group.py').read_bytes()).hexdigest(),
            'probe_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'fork_enabled_only_in_fixed_owned_probe':True,'production_fork_supported':False,
            'emergency_eof_used_before_positive_drain':False if confirmed else None,
            'elapsed_seconds':round(time.monotonic()-started,6),'provider_requests':0,
            'credential_file_read':False,'real_budget_opened':False})
        return result


if __name__=='__main__':print(json.dumps(probe(),indent=2))
