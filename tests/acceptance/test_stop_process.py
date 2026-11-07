"""Actual separate CLI, lock contention, SIGKILL and new-PID stop observation."""
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


class StopProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.db=self.base/'state.sqlite';self.marker=self.base/'barrier';self.release=self.base/'release'
        self.env={'PATH':os.defpath,'LANG':'C'}

    def spawn(self,mode,tid=None):
        args=[sys.executable,'-m','tests.acceptance.stop_child',str(self.root),str(self.db),mode,str(self.marker),str(self.release)]
        if tid:args.append(tid)
        p=subprocess.Popen(args,cwd=str(REPO),env=self.env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        def cleanup():
            if p.poll() is None:p.kill()
            p.communicate(timeout=5)
        self.addCleanup(cleanup);return p

    def wait_barrier(self,p):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if self.marker.exists():
                try:return json.loads(self.marker.read_text())
                except json.JSONDecodeError:pass
            if p.poll() is not None:self.fail('child ended before barrier: '+p.communicate()[1])
            time.sleep(.01)
        self.fail('barrier timeout')

    def request(self,barrier):
        result=subprocess.run([sys.executable,'-m','agent_lab','--db',str(self.db),'stop',barrier['task_id'],
                               '--turn-id',barrier['turn_id'],'--workspace',str(self.root)],
                              cwd=str(REPO),env=self.env,text=True,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        receipt=json.loads(result.stdout)
        self.assertEqual(receipt['request']['status'],'requested')
        self.assertEqual(receipt['observation'],'deferred_until_checkpoint')
        self.assertFalse(receipt['current_call_preempted'])
        return receipt

    def test_live_claimed_call_finishes_but_new_tools_never_start(self):
        child=self.spawn('wait_model');barrier=self.wait_barrier(child);self.request(barrier)
        self.assertIsNone(child.poll())
        self.release.write_text('finish current call')
        out,err=child.communicate(timeout=10);self.assertEqual(child.returncode,0,err)
        result=json.loads(out)
        self.assertEqual(result['reason'],'user_stop_requested')
        self.assertEqual(result['model_calls_this_process'],1)
        self.assertFalse((self.root/'first').exists());self.assertFalse((self.root/'second').exists())
        with Store.open_readonly(self.db) as store:
            control=store.stop_request(barrier['task_id'],barrier['turn_id'])
            state=store.load(barrier['task_id'])
            self.assertEqual(control['status'],'observed')
            self.assertEqual(state['metrics']['tool_attempts'],0)
            self.assertEqual(state['metrics']['input_tokens'],100)
        print(json.dumps({'kind':'cooperative_stop_process_contract','model_kind':'offline_protocol_double',
              'scenario':'claimed_call_finishes','runner_pid':child.pid,'stop_observed':True,
              'current_call_preempted':False,'new_file_effects':0}),flush=True)

    def restart_after_kill(self,mode):
        child=self.spawn(mode);barrier=self.wait_barrier(child);self.request(barrier)
        before=(self.root/'first').stat() if (self.root/'first').exists() else None
        os.kill(child.pid,signal.SIGKILL);child.communicate(timeout=5)
        self.assertEqual(child.returncode,-signal.SIGKILL)
        resumed=self.spawn('resume',barrier['task_id']);out,err=resumed.communicate(timeout=10)
        self.assertEqual(resumed.returncode,0,err);self.assertNotEqual(child.pid,resumed.pid)
        result=json.loads(out)
        self.assertEqual(result['reason'],'stop_requested_unconfirmed_effect')
        self.assertEqual(result['phase'],'needs_confirmation');self.assertEqual(result['model_calls_this_process'],0)
        self.assertFalse((self.root/'second').exists())
        if before:
            after=(self.root/'first').stat()
            self.assertEqual((before.st_ino,before.st_mtime_ns),(after.st_ino,after.st_mtime_ns))
            self.assertEqual((self.root/'first').read_text(),'x\n')
        else:self.assertFalse((self.root/'first').exists())
        with Store.open_readonly(self.db) as store:
            state=store.load(barrier['task_id'])
            self.assertTrue(state['outcome_unresolved']);self.assertIsNotNone(state['pending'])
            self.assertEqual(store.stop_request(barrier['task_id'],barrier['turn_id'])['status'],'observed')
        print(json.dumps({'kind':'cooperative_stop_process_contract','model_kind':'offline_protocol_double',
              'scenario':mode,'killed_pid':child.pid,'kill_return_code':child.returncode,'resumed_pid':resumed.pid,
              'phase':'needs_confirmation','new_effects_after_stop':0,'unknown_pending_preserved':True}),flush=True)

    def test_kill_after_claim_before_effect_never_recovers_by_creating_file(self):
        self.restart_after_kill('after_tool_claim')

    def test_kill_after_patch_effect_never_replays_or_executes_remaining_files(self):
        self.restart_after_kill('after_patch_effect')
