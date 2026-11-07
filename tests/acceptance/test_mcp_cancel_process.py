"""Actual CLI stop and new Runner; external effect may survive local close."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from agent_lab.store import Store
from tests.fixtures.mcp_http_server import serving

REPO=Path(__file__).resolve().parents[2]
CHILD=Path(__file__).with_name('mcp_cancel_child.py')
SERVER=Path(__file__).resolve().parents[1]/'fixtures/mcp_server.py'


class MCPCancelProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-mcp-stop-',dir='/private/tmp');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir();self.db=self.base/'state.sqlite';self.config=self.base/'mcp.json';self.ready=self.base/'ready.json'
        self.env={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}

    def command(self,mode):return [sys.executable,'-I',str(CHILD),mode,str(self.root),str(self.db),str(self.config),str(self.ready)]

    def stop_and_resume(self,effect_ready,before_resume=None):
        runner=subprocess.Popen(self.command('run'),cwd=REPO,env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+8
            while not (self.ready.exists() and effect_ready()):
                self.assertIsNone(runner.poll(),'Runner ended before the external effect barrier')
                if time.monotonic()>=deadline:self.fail('external effect barrier timeout')
                time.sleep(.01)
            ids=json.loads(self.ready.read_text());started=time.monotonic()
            stop=subprocess.run([sys.executable,'-m','agent_lab','--db',str(self.db),'stop',ids['task_id'],
                '--workspace',str(self.root),'--turn-id',ids['turn_id']],cwd=REPO,env=self.env,capture_output=True,text=True,timeout=3)
            self.assertEqual(stop.returncode,0,stop.stderr)
            out,err=runner.communicate(timeout=3);self.assertEqual(runner.returncode,0,err)
            self.assertLess(time.monotonic()-started,2)
            first=json.loads(out);self.assertEqual(first['phase'],'needs_confirmation')
            self.assertEqual(first['reason'],'tool_cancel_unconfirmed')
            self.assertEqual(first['cancellation']['local_quiescent'],False)
            self.assertTrue(first['cancellation']['effects_unknown'])
            if before_resume is not None:before_resume()
            restarted=subprocess.run(self.command('resume'),cwd=REPO,env=self.env,capture_output=True,text=True,timeout=4)
            self.assertEqual(restarted.returncode,0,restarted.stderr);second=json.loads(restarted.stdout)
            self.assertNotEqual(first['pid'],second['pid'])
            self.assertEqual(second['phase'],'needs_confirmation');self.assertEqual(first['pending_id'],second['pending_id'])
            self.assertEqual(second['model_calls'],1)
            with Store.open_readonly(self.db) as store:
                state=store.load(ids['task_id']);self.assertEqual(state['pending']['status'],'running')
                events=store.events(ids['task_id']);self.assertTrue(any(e['kind']=='tool_cancelled' for e in events))
            return first,second
        finally:
            if runner.poll() is None:runner.kill();runner.communicate(timeout=2)

    def test_stdio_effect_then_cli_stop_new_process_does_not_replay(self):
        marker=self.base/'effect';log=self.base/'requests'
        self.config.write_text(json.dumps({'servers':[{'name':'owned','command':[sys.executable,'-I','-S',str(SERVER),'effect_timeout',str(log),str(marker)],'cwd':str(self.base),'timeout':8}]}))
        first,second=self.stop_and_resume(marker.exists)
        self.assertEqual(marker.read_text(),'effect\n')
        records=[json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(sum(r.get('method')=='tools/call' for r in records),1)
        print(json.dumps({'contract':'stdio_mcp_cli_stop_new_pid','first_pid':first['pid'],'resumed_pid':second['pid'],
            'effect_count':1,'pending_id_preserved':True,'external_quiescence_claimed':False,'real_llm_quality':None}))

    def test_http_remote_effect_after_local_cancel_remains_unknown_on_new_process(self):
        with serving() as server:
            self.config.write_text(json.dumps({'servers':[{'name':'owned','transport':'streamable_http','url':server.url,'allow_loopback_http':True,'timeout':8}]}))
            server.mode='effect_timeout';server.effect_wait_seconds=8;late=self.base/'late-external-effect';server.late_effect_path=late
            def cancelled_connection_closed():
                self.assertFalse(any(r.get('method')=='DELETE' for r in server.records))
            first,second=self.stop_and_resume(server.effect_ready.is_set,cancelled_connection_closed)
            self.assertFalse(server.release.is_set());self.assertFalse(late.exists())
            server.release.set();deadline=time.monotonic()+1
            while not late.exists() and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(late.read_text(),'external effect after local cancel\n')
            self.assertEqual(len(server.effects),1)
            # The new Runner initializes a distinct client and closes it
            # normally. Its DELETE is not a cancellation/ack of the old call.
            self.assertEqual(sum(r.get('method')=='DELETE' for r in server.records),1)
            self.assertEqual(sum(r.get('body',{}).get('method')=='initialize' for r in server.records),2)
            print(json.dumps({'contract':'http_mcp_cli_stop_new_pid','first_pid':first['pid'],'resumed_pid':second['pid'],
                'effect_count':1,'late_external_effect_observed':True,'pending_id_preserved':True,'external_quiescence_claimed':False,'real_llm_quality':None}))


if __name__=='__main__':unittest.main()
