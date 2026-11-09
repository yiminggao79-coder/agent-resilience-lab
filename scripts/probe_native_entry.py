"""Research only: native Python entry under NoFork plus explicit spawn denial."""
import errno
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_lab import sandbox

BODY='''import errno,json,os,sys
result={'native_entry_started':True,'base_prefix':sys.prefix==sys.base_prefix}
try:
 pid=os.fork()
 if pid==0:os._exit(0)
 os.waitpid(pid,0);result['fork_errno']=None
except OSError as error:result['fork_errno']=error.errno
try:
 pid=os.posix_spawn('/usr/bin/true',['true'],{})
 os.waitpid(pid,0);result['spawn_errno']=None
except OSError as error:result['spawn_errno']=error.errno
print(json.dumps(result))
'''


def probe():
    native=Path(sys.base_prefix)/'Resources/Python.app/Contents/MacOS/Python'
    if not native.is_file() or not os.access(native,os.X_OK):raise RuntimeError('native_entry_unavailable')
    observations=[]
    with tempfile.TemporaryDirectory(prefix='agentlab-native-entry-',dir='/private/tmp') as directory:
        base=Path(directory);root=base/'work';root.mkdir();scratch=base/'scratch';scratch.mkdir()
        original=sandbox._profile(root,scratch)
        # Keep NoFork (deny default, no allow process-fork). The probe can at
        # worst create an immediately exiting child or run /usr/bin/true once,
        # never arbitrary child code; unexpected success is reaped explicitly.
        for label,modifier in [('eperm',''),('enosys',' (with errno ENOSYS)')]:
            profile=original+'\n(deny syscall-unix (syscall-number 244)'+modifier+')'
            result=sandbox._collect([sandbox.BACKEND_PATH,'-p',profile,str(native),'-I','-c',BODY],root,
                {'PATH':os.defpath,'LANG':'C.UTF-8','HOME':str(scratch),'TMPDIR':str(scratch),
                 'PYTHONDONTWRITEBYTECODE':'1'},4)
            body=json.loads(result['stdout']) if result['exit_code']==0 else None
            observations.append({'variant':label,'exit_code':result['exit_code'],'body':body,
                'stderr_bytes':len(result['stderr'].encode()),'stdout_truncated':result['stdout_truncated'],
                'profile_sha256':hashlib.sha256(profile.encode()).hexdigest()})
    return {'kind':'research_native_entry_nofork','observations':observations,
            'expected_enosys_errno':errno.ENOSYS,'production_policy_changed':False,'fork_enabled':False,
            'provider_requests':0,'credential_file_read':False,'real_budget_opened':False}


if __name__=='__main__':print(json.dumps(probe(),indent=2))
