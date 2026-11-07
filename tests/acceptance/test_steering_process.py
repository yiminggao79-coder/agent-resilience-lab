"""Actual SIGKILL/reopen; no model API, no tool subprocess substitution."""
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

from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel

CHILD=Path(__file__).with_name('steering_child.py')


class SteeringProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.db=self.base/'private.sqlite';self.marker=self.base/'barrier'
        with Store(self.db) as store:
            self.tid=Engine(store,Tools(self.root),ScriptedModel([])).create('initial',Limits(max_seconds=60))
            self.turn=store.load(self.tid)['turn_id']

    def child(self,mode):
        p=subprocess.Popen([sys.executable,str(CHILD),str(self.db),str(self.root),self.tid,mode,str(self.marker)],
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def cleanup():
            if p.poll() is None:p.kill()
            p.communicate(timeout=5)
        self.addCleanup(cleanup)
        return p

    def wait_marker(self,p):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if self.marker.exists():return
            if p.poll() is not None:self.fail('child ended before barrier: '+p.communicate()[1])
            time.sleep(.01)
        self.fail('child barrier timeout')

    def test_second_process_admission_while_runner_locked_survives_unknown_request_kill(self):
        p=self.child('waiting_model');self.wait_marker(p)
        rid=uuid.uuid4().hex
        # A genuinely separate sender process, while Runner owns workspace lock.
        cmd=[sys.executable,'-m','agent_lab','--db',str(self.db),'steer',self.tid,
             '--turn-id',self.turn,'--request-id',rid]
        sender=subprocess.run(cmd,input='persisted feedback',text=True,capture_output=True,timeout=5)
        self.assertEqual(sender.returncode,0,sender.stderr)
        self.assertEqual(json.loads(sender.stdout)['status'],'pending')
        os.kill(p.pid,signal.SIGKILL);p.communicate(timeout=5);self.assertEqual(p.returncode,-signal.SIGKILL)
        resumed=self.child('normal');out,err=resumed.communicate(timeout=5)
        self.assertEqual(resumed.returncode,0,err);self.assertNotEqual(p.pid,resumed.pid)
        self.assertEqual(json.loads(out)['phase'],'needs_confirmation')
        with Store(self.db) as store:
            self.assertEqual(store.inputs(self.tid)[0]['status'],'pending')
            self.assertNotIn('persisted feedback',json.dumps(store.load(self.tid)['messages']))
        print(json.dumps({'kind':'steering_process_contract','model_kind':'offline_protocol_double',
            'scenario':'admitted_while_runner_locked_then_request_unknown','killed_pid':p.pid,
            'kill_return_code':p.returncode,'resumed_pid':resumed.pid,'phase':'needs_confirmation',
            'input_status':'pending','message_applied':False}),flush=True)

    def test_kill_after_atomic_application_new_pid_never_duplicates_input(self):
        with Store(self.db) as store:store.queue_input(self.tid,self.turn,'once only',uuid.uuid4().hex)
        p=self.child('after_applied');self.wait_marker(p)
        with Store(self.db) as store:
            before=store.load(self.tid);receipt=store.inputs(self.tid)[0]
            self.assertEqual(receipt['status'],'applied');self.assertEqual(before['phase'],'ready')
        os.kill(p.pid,signal.SIGKILL);p.communicate(timeout=5);self.assertEqual(p.returncode,-signal.SIGKILL)
        resumed=self.child('normal');out,err=resumed.communicate(timeout=5)
        self.assertEqual(resumed.returncode,0,err);self.assertNotEqual(p.pid,resumed.pid)
        self.assertEqual(json.loads(out)['phase'],'completed')
        with Store(self.db) as store:
            after=store.load(self.tid)
            self.assertEqual([m.get('content') for m in after['messages']].count('once only'),1)
            self.assertEqual(store.inputs(self.tid)[0],receipt)
            self.assertEqual(len([e for e in store.events(self.tid) if e['kind']=='input_applied']),1)
        print(json.dumps({'kind':'steering_process_contract','model_kind':'offline_protocol_double',
            'scenario':'kill_after_atomic_application','killed_pid':p.pid,'kill_return_code':p.returncode,
            'resumed_pid':resumed.pid,'phase':'completed','input_status':'applied',
            'message_occurrences':1,'application_events':1}),flush=True)
