"""Actual native job stop/owner-death proof, not real model task quality."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from agent_lab.store import Store

REPO=Path(__file__).resolve().parents[2]


class ToolCancelProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.db=self.base/'state.sqlite';self.marker=self.base/'runner.json';self.ready=self.root/'native-ready.json'
        self.env={'PATH':os.defpath,'LANG':'C'}

    def spawn(self,mode='start',tid=None):
        args=[sys.executable,'-m','tests.acceptance.tool_cancel_child',str(self.root),str(self.db),mode,str(self.marker)]
        if tid:args.append(tid)
        p=subprocess.Popen(args,cwd=str(REPO),env=self.env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        def cleanup():
            if p.poll() is None:p.kill()
            p.communicate(timeout=5)
        self.addCleanup(cleanup);return p

    def wait_ready(self,p):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if self.marker.exists() and self.ready.exists():
                try:return json.loads(self.marker.read_text()),json.loads(self.ready.read_text())
                except json.JSONDecodeError:pass
            if p.poll() is not None:self.fail('Runner ended before native barrier: '+p.communicate()[1])
            time.sleep(.01)
        self.fail('native process did not reach barrier')

    @staticmethod
    def running(pid):
        state=subprocess.run(['/bin/ps','-o','stat=','-p',str(pid)],text=True,capture_output=True,timeout=2).stdout.strip()
        return bool(state) and not state.startswith('Z')

    def assert_quiet(self,native):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and any(self.running(native[k]) for k in ('native_pid','supervisor_pid')):time.sleep(.02)
        self.assertFalse(self.running(native['native_pid']))
        self.assertFalse(self.running(native['supervisor_pid']))
        self.assertFalse((self.root/'late').exists());self.assertFalse((self.root/'tail').exists())

    def stopped_native(self,mode):
        p=self.spawn(mode);marker,native=self.wait_ready(p);started=time.monotonic()
        result=subprocess.run([sys.executable,'-m','agent_lab','--db',str(self.db),'stop',marker['task_id'],
                               '--turn-id',marker['turn_id'],'--workspace',str(self.root)],cwd=str(REPO),env=self.env,
                              capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        out,err=p.communicate(timeout=5);self.assertEqual(p.returncode,0,err)
        state=json.loads(out)
        self.assertEqual((state['phase'],state['reason']),('needs_confirmation','tool_cancelled_effect_unknown'))
        self.assertTrue(state['last_tool_cancellation']['local_quiescent']);self.assertLess(time.monotonic()-started,5)
        self.assert_quiet(native)
        with Store.open_readonly(self.db) as store:
            saved=store.load(marker['task_id'])
            self.assertTrue(saved['outcome_unresolved']);self.assertEqual(saved['pending']['operation_id'],marker['operation_id'])
            self.assertEqual([c['id'] for c in saved['tool_queue']],['tail'])
            self.assertEqual(store.stop_request(marker['task_id'],marker['turn_id'])['status'],'observed')
        print(json.dumps({'kind':'native_tool_stop_process_contract','model_kind':'offline_protocol_double',
            'tool':'run_tests' if mode=='tests' else 'run_command','runner_pid':p.pid,**native,'native_and_supervisor_nonrunning':True,'known_prefix_file':'native-ready.json',
            'late_or_tail_effect':False,'phase':state['phase'],'effects':'unknown'}),flush=True)

    def test_cli_stop_waits_for_command_cleanup_but_retains_unknown_effect(self):
        self.stopped_native('start')

    def test_cli_stop_cancels_real_unittest_and_keeps_effects_unknown(self):
        self.stopped_native('tests')

    def test_runner_sigkill_owner_eof_cleans_native_before_new_runner_recovery(self):
        p=self.spawn();marker,native=self.wait_ready(p);os.kill(p.pid,signal.SIGKILL);p.communicate(timeout=5)
        self.assertEqual(p.returncode,-signal.SIGKILL);self.assert_quiet(native)
        with Store.open_readonly(self.db) as store:
            interrupted=store.load(marker['task_id']);self.assertEqual(interrupted['phase'],'tool_pending')
            self.assertEqual(interrupted['pending']['status'],'running');self.assertNotIn('last_tool_cancellation',interrupted)
        # A real new Runner acquires/rotates the execution fence only after the
        # old supervisor releases its lease. Unsafe recovery never reruns it.
        resumed=self.spawn('resume',marker['task_id']);out,err=resumed.communicate(timeout=5)
        self.assertEqual(resumed.returncode,0,err);self.assertNotEqual(resumed.pid,p.pid)
        self.assertEqual(json.loads(out)['phase'],'needs_confirmation')
        self.assertFalse((self.root/'late').exists());self.assertFalse((self.root/'tail').exists())
        print(json.dumps({'kind':'native_tool_owner_death_process_contract','model_kind':'offline_protocol_double',
            'killed_runner_pid':p.pid,'kill_return_code':p.returncode,'resumed_pid':resumed.pid,**native,
            'native_and_supervisor_nonrunning':True,'new_runner_fence_acquired':True,
            'pending_unknown_preserved':True,'new_effects':False}),flush=True)
