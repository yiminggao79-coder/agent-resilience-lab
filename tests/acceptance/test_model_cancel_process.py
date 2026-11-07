"""Actual CLI stop and Runner SIGKILL against owned synthetic HTTP barriers."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from agent_lab.store import Store
from tests.dev.test_model_cancellation import BarrierHandler, sse

REPO = Path(__file__).resolve().parents[2]


class ModelCancelProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.root = self.base/'work'; self.root.mkdir()
        self.db = self.base/'state.sqlite'; self.marker = self.base/'marker.json'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), BarrierHandler); self.server.daemon_threads = True
        self.server.requests = []
        self.server.started, self.server.release, self.server.final_sent = threading.Event(), threading.Event(), threading.Event()
        self.server.prefix = sse({'choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'id':'partial','type':'function','function':{'name':'write_file','arguments':'{"path":"never",'}}]}}]})
        self.server.suffix = b'data: [DONE]\n\n'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown); self.addCleanup(self.server.release.set)
        self.env = {'PATH': os.defpath, 'LANG':'C'}

    def spawn(self, stream=True):
        if not stream:
            self.server.prefix=b'{"choices":'; self.server.suffix=b'[]}'
        p = subprocess.Popen([sys.executable,'-m','tests.acceptance.model_cancel_child', str(self.root),str(self.db),str(self.marker),
                              'http://127.0.0.1:%d/v1'%self.server.server_port, 'true' if stream else 'false'],
                             cwd=str(REPO),env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def cleanup():
            if p.poll() is None: p.kill()
            p.communicate(timeout=5)
        self.addCleanup(cleanup)
        self.assertTrue(self.server.started.wait(5), 'worker did not reach owned HTTP barrier')
        return p, json.loads(self.marker.read_text())

    @staticmethod
    def worker_running(pid):
        # Metadata for this exact owned child only; zombies have stopped execution.
        result = subprocess.run(['/bin/ps','-o','stat=','-p',str(pid)],capture_output=True,text=True,timeout=2)
        status = result.stdout.strip()
        return bool(status) and not status.startswith('Z')

    def test_cli_stop_cancels_live_model_before_provider_final(self):
        for stream in (True, False):
            with self.subTest(stream=stream):
                self.server.started.clear(); self.server.final_sent.clear()
                p, marker = self.spawn(stream)
                stopped = subprocess.run([sys.executable,'-m','agent_lab','--db',str(self.db),'stop',marker['task_id'],
                            '--turn-id',marker['turn_id'],'--workspace',str(self.root)],cwd=str(REPO),env=self.env,
                            capture_output=True,text=True,timeout=5)
                self.assertEqual(stopped.returncode,0,stopped.stderr)
                out,err = p.communicate(timeout=3); self.assertEqual(p.returncode,0,err)
                self.assertEqual(json.loads(out)['reason'],'user_stop_requested')
                self.assertFalse(self.server.final_sent.is_set()); self.assertFalse(self.worker_running(marker['worker_pid']))
                self.assertEqual({entry.name for entry in self.root.iterdir()}, {'.agent-lab.lock', '.agent-lab.execution.lock', '.agent-lab.generation'})
                with Store.open_readonly(self.db) as store:
                    state=store.load(marker['task_id'])
                    self.assertEqual(state['phase'],'failed'); self.assertTrue(state['usage_unknown'])
                    self.assertIsNone(state['metrics']['input_tokens']); self.assertFalse(state.get('outcome_unresolved',False))
                    self.assertEqual(state['metrics']['tool_attempts'],0)
                    self.assertEqual(store.stop_request(marker['task_id'],marker['turn_id'])['status'],'observed')
                print(json.dumps({'kind':'model_cancel_process_contract','provider':'loopback_synthetic','stream':stream,
                    'runner_pid':p.pid,'worker_pid':marker['worker_pid'],'worker_nonrunning_before_final':True,
                    'tools_accepted':False,'usage':'unknown'}),flush=True)

    def test_runner_sigkill_reclaims_worker_without_waiting_for_provider(self):
        p, marker = self.spawn()
        self.assertTrue(self.worker_running(marker['worker_pid']))
        os.kill(p.pid,signal.SIGKILL); p.communicate(timeout=3)
        self.assertEqual(p.returncode,-signal.SIGKILL)
        deadline=time.monotonic()+2
        while self.worker_running(marker['worker_pid']) and time.monotonic()<deadline: time.sleep(.02)
        self.assertFalse(self.worker_running(marker['worker_pid']), 'HTTP worker survived owner EOF')
        self.assertFalse(self.server.final_sent.is_set()); self.assertEqual({entry.name for entry in self.root.iterdir()}, {'.agent-lab.lock', '.agent-lab.execution.lock', '.agent-lab.generation'})
        with Store.open_readonly(self.db) as store:
            self.assertEqual(store.load(marker['task_id'])['phase'],'model_pending')
        print(json.dumps({'kind':'model_worker_owner_death_contract','provider':'loopback_synthetic',
            'killed_runner_pid':p.pid,'worker_pid':marker['worker_pid'],'runner_return_code':p.returncode,
            'worker_nonrunning_before_final':True,'checkpoint':'model_pending_unconfirmed'}),flush=True)
