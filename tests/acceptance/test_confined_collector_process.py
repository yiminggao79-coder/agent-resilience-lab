"""Candidate-only cross-process durable proofs with finite owned native actors.

No production switch, paid provider, user projects or arbitrary PID cleanup.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from agent_lab import native_jobs
from agent_lab.native_job_runtime import worker_job, supervisor_payload, SUPERVISOR_ENV
from agent_lab.process_guard import rotate_generation
from agent_lab.store import Store

REPO = Path(__file__).resolve().parents[2]
SUPERVISOR = r'''
import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from agent_lab import sandbox
from agent_lab.confined_collector import collect
from agent_lab.native_job_runtime import supervisor_job
from agent_lab.process_group import OwnedGroup,GroupObservationError
from agent_lab.tool_control import inherited_owner,NativeCancelled
import agent_lab.confined_collector as candidate
from agent_lab.tools import ToolError
root,scratch,mode=Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4]
profile=sandbox._command_profile(root,scratch)
profile+='\n(allow process-fork)\n(deny syscall-unix (syscall-number 66 82 147 244))'
native=Path(sys.base_prefix)/'Resources/Python.app/Contents/MacOS/Python'
if mode=='observation_failure':
 class Broken(OwnedGroup):
  def anchor_only_zombie(self):raise GroupObservationError()
 candidate.OwnedGroup=Broken
body="print('finite owned child')"
if mode in ('death','owner_death'):
 body="""import os,time
from pathlib import Path
until=time.monotonic()+1.5
child=os.fork()
for fd in (0,1,2):os.close(fd)
if child:
 Path('ready.tmp').write_text(str(until));os.replace('ready.tmp','ready')
while time.monotonic()<until and not Path('release').exists():time.sleep(.003)
while time.monotonic()<until:
 label='effect-parent' if child else 'effect-child'
 Path(label+'.tmp').write_text(str(time.monotonic_ns()));os.replace(label+'.tmp',label)
 time.sleep(.01)
os._exit(0)
"""
if mode=='owner_death':body=body.replace('time.monotonic()+1.5','time.monotonic()+3.0')
env={'PATH':os.defpath,'HOME':str(scratch),'TMPDIR':str(scratch),'LANG':'C.UTF-8',
     'PYTHONDONTWRITEBYTECODE':'1'}
with inherited_owner(),supervisor_job(root):
 try:
  value=collect([sandbox.BACKEND_PATH,'-p',profile,str(native),'-I','-c',body],
                root,env,3,cleanup_timeout=.08 if mode=='observation_failure' else 1)
  print(json.dumps({'ok':value['ok']}),flush=True)
 except ToolError as error:
  print(json.dumps({'error':error.code}),flush=True)
 except NativeCancelled:
  print(json.dumps({'cancelled':True}),flush=True)
'''
WORKER = r'''
import json,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tests.acceptance.test_confined_collector_process import SUPERVISOR
from agent_lab.native_job_runtime import worker_job,supervisor_payload,SUPERVISOR_ENV
from agent_lab.tool_control import OwnerPipe,OWNER_ENV
root,scratch=Path(sys.argv[2]),Path(sys.argv[3])
reference=json.loads(os.environ.pop('AGENT_LAB_FIXTURE_REFERENCE'))
with worker_job(reference,root) as worker,OwnerPipe() as owner:
 worker.supervisor_begin()
 env={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1',
      SUPERVISOR_ENV:supervisor_payload(),OWNER_ENV:str(owner.read)}
 child=subprocess.Popen([str(Path(sys.executable).resolve()),'-I','-c',SUPERVISOR,
                         sys.argv[1],str(root),str(scratch),'owner_death'],env=env,
                        pass_fds=(owner.read,),stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 raise SystemExit(child.wait(timeout=5))
'''
REOPEN = r'''
import json,sys
sys.path.insert(0,sys.argv[1])
from agent_lab import native_jobs
from pathlib import Path
root,db=Path(sys.argv[2]),Path(sys.argv[3])
report=native_jobs.inspect_workspace(root,db)
try:native_jobs.ensure_workspace_idle(root,db);blocked=False
except native_jobs.JobBlocked:blocked=True
print(json.dumps({'state':report['state'],'workspace_quarantined':report['workspace_quarantined'],
                  'admission_blocked':blocked,'pid':__import__('os').getpid()}))
'''


class ConfinedCollectorProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agentlab-confined-q-', dir='/private/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.root = self.base/'work'; self.root.mkdir()
        self.scratch = self.base/'scratch'; self.scratch.mkdir()
        self.db = self.base/'private.sqlite'
        generation = rotate_generation(self.root)
        identity = lambda: uuid.uuid4().hex
        intent = {'name': 'run_command', 'args': {}, 'policy': 'candidate_owned_fixture'}
        state = {'id': identity(), 'turn_id': identity(), 'workspace': str(self.root),
                 'phase': 'tool_pending', 'pending': {'status': 'running', 'operation_id': identity(), 'intent': intent},
                 'created_at': 1, 'messages': [], 'limits': {}, 'metrics': {}}
        with Store(self.db) as store:
            store.save(state, 'controlled_fixture')
        self.reference = native_jobs.claim_job(self.root, self.db, state['id'], state['turn_id'],
                                             state['pending']['operation_id'], generation, intent)
        self.clean_env = {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}

    def start(self, mode):
        env = {**self.clean_env, SUPERVISOR_ENV: supervisor_payload()}
        return subprocess.Popen([str(Path(sys.executable).resolve()), '-I', '-c', SUPERVISOR,
                                 str(REPO), str(self.root), str(self.scratch), mode], env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def reopen(self):
        result = subprocess.run([str(Path(sys.executable).resolve()), '-I', '-c', REOPEN,
                                 str(REPO), str(self.root), str(self.db)], env=self.clean_env,
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertNotEqual(report.pop('pid'), os.getpid())
        return report

    def test_success_receipt_is_durable_in_new_process(self):
        with worker_job(self.reference, self.root) as worker:
            worker.supervisor_begin()
            proc = self.start('success')
            out, err = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 0, err)
            self.assertEqual(json.loads(out), {'ok': True})
        self.assertEqual(self.reopen(), {'state': 'quiescent', 'workspace_quarantined': False, 'admission_blocked': False})

    def test_failed_observation_is_durable_quarantine_not_a_cleanup_receipt(self):
        with worker_job(self.reference, self.root) as worker:
            worker.supervisor_begin()
            proc = self.start('observation_failure')
            out, err = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 0, err)
            self.assertEqual(json.loads(out), {'error': 'native_group_unconfirmed'})
        self.assertEqual(self.reopen(), {'state': 'claimed', 'workspace_quarantined': True, 'admission_blocked': True})

    def test_sigkill_supervisor_keeps_quarantine_despite_later_guest_effects(self):
        with worker_job(self.reference, self.root) as worker:
            worker.supervisor_begin()
            proc = self.start('death')
            try:
                deadline = time.monotonic()+3
                while not (self.root/'ready').exists():
                    if time.monotonic() >= deadline:
                        self.fail('finite owned native barrier not reached')
                    time.sleep(.01)
                # Own unreaped supervisor Popen, no arbitrary PID/group lookup.
                os.kill(proc.pid, signal.SIGKILL)
                _, err = proc.communicate(timeout=3)
                self.assertEqual(proc.returncode, -signal.SIGKILL, err)
                self.assertEqual(self.reopen(), {'state': 'claimed', 'workspace_quarantined': True, 'admission_blocked': True})
                (self.root/'release').write_text('owned fixture release after supervisor death')
                deadline = time.monotonic()+.5
                while not all((self.root/name).exists() for name in ('effect-parent', 'effect-child')):
                    if time.monotonic() >= deadline:
                        self.fail('owned fixture did not demonstrate post-supervisor effects')
                    time.sleep(.01)
                self.assertEqual(self.reopen()['workspace_quarantined'], True)
            finally:
                # Finite actors exit on their own; this is fixture hygiene, not
                # collector cleanup or a quiescence receipt. No raw PID kills.
                proc.wait(timeout=4)
                time.sleep(1.65)

    def test_sigkill_worker_closes_owner_pipe_and_live_supervisor_confirms_drain(self):
        confirmed = False
        # Host clock only. Do not compare subprocess-local monotonic epochs.
        started_at = time.monotonic()
        proc = subprocess.Popen([str(Path(sys.executable).resolve()), '-I', '-c', WORKER,
                                 str(REPO), str(self.root), str(self.scratch)],
                                env={**self.clean_env, 'AGENT_LAB_FIXTURE_REFERENCE': json.dumps(self.reference)},
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic()+3
            while not (self.root/'ready').exists():
                if time.monotonic() >= deadline:
                    self.fail('owned worker/native barrier not reached')
                time.sleep(.01)
            (self.root/'release').write_text('release finite owned actors')
            deadline = time.monotonic()+.5
            while not all((self.root/name).exists() for name in ('effect-parent','effect-child')):
                if time.monotonic() >= deadline:
                    self.fail('owned effects did not start before worker kill')
                time.sleep(.005)
            killed_at = time.monotonic()
            os.kill(proc.pid, signal.SIGKILL)
            _, err = proc.communicate(timeout=3)
            self.assertEqual(proc.returncode, -signal.SIGKILL, err)
            deadline = killed_at+1.5
            while self.reopen()['workspace_quarantined']:
                if time.monotonic() >= deadline:
                    self.fail('live supervisor failed to publish owned drain after worker death')
                time.sleep(.02)
            # Cannot pass by waiting for the fixture's cooperative self-exit.
            # Native lifetime starts after this Host timestamp, so its earliest
            # possible self-exit is started_at+3. This is a conservative bound.
            self.assertLess(time.monotonic(), started_at+2.5)
            confirmed = True
            before = {p.name:p.read_bytes() for p in self.root.glob('effect-*')}
            time.sleep(.12)
            self.assertEqual({p.name:p.read_bytes() for p in self.root.glob('effect-*')}, before)
            self.assertEqual(self.reopen(), {'state':'quiescent','workspace_quarantined':False,'admission_blocked':False})
        finally:
            proc.wait(timeout=5)
            # Only for finite fixture hygiene if the assertion failed.
            if not confirmed:
                time.sleep(3.15)


if __name__ == '__main__':
    unittest.main()
