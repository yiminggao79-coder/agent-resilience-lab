"""Actual large delete effect→SIGKILL→new PID state reconciliation."""
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import unittest

from agent_lab.store import Store
from tests.dev.test_file_operations import CRASH_DRIVER

REPO=Path(__file__).resolve().parents[2]


class LargeDeleteProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.path=self.root/'remove.txt';self.content=b'\x00binary data\xff'*18000
        self.path.write_bytes(self.content);self.path.chmod(0o755)
        self.assertGreater(self.path.stat().st_size,65536)
        self.db=self.base/'private.sqlite';(self.root/'keep').write_text('unchanged')

    def spawn(self,mode,tid=None):
        args=[sys.executable,'-c',CRASH_DRIVER,str(self.root),str(self.db),mode]
        if tid:args.append(tid)
        child=subprocess.Popen(args,cwd=str(REPO),env={'PATH':os.defpath,'LANG':'C'},text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        def cleanup():
            if child.poll() is None:child.kill()
            child.communicate(timeout=5)
        self.addCleanup(cleanup);return child

    def crash(self):
        child=self.spawn('crash')
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout,selectors.EVENT_READ)
            self.assertTrue(selector.select(10),'delete did not reach effect boundary')
        line=child.stdout.readline();self.assertTrue(line,'ended before barrier')
        tid=json.loads(line)['task_id'];self.assertFalse(self.path.exists())
        with Store.open_readonly(self.db) as store:
            pending=store.load(tid)['pending']
            self.assertEqual(pending['status'],'running');self.assertEqual(pending['intent']['before_mode'],0o755)
            self.assertLess(len(json.dumps(pending['intent'])),1024)
        os.kill(child.pid,signal.SIGKILL);child.communicate(timeout=5)
        self.assertEqual(child.returncode,-signal.SIGKILL)
        return child,tid

    def test_large_delete_absence_reconciles_after_kill_without_new_effect(self):
        killed,tid=self.crash();resumed=self.spawn('resume',tid)
        out,err=resumed.communicate(timeout=10);self.assertEqual(resumed.returncode,0,err)
        state=json.loads(out);self.assertEqual(state['phase'],'completed');self.assertNotEqual(killed.pid,resumed.pid)
        self.assertFalse(self.path.exists());self.assertEqual((self.root/'keep').read_text(),'unchanged')
        with Store.open_readonly(self.db) as store:
            saved=store.load(tid);self.assertEqual(saved['metrics']['recoveries'],1)
            results=[json.loads(m['content']) for m in saved['messages'] if m['role']=='tool']
            self.assertEqual(results,[{'ok':True,'deleted':True,'reconciled':True}])
        print(json.dumps({'kind':'large_delete_process_contract','model_kind':'offline_protocol_double',
              'scenario':'absence_after_effect','file_bytes':len(self.content),'killed_pid':killed.pid,
              'kill_return_code':killed.returncode,'resumed_pid':resumed.pid,'phase':state['phase'],
              'intent_excludes_content':True,'new_delete_effect':False}),flush=True)

    def test_recreated_large_target_changed_mode_is_preserved_after_restart(self):
        killed,tid=self.crash();self.path.write_bytes(self.content);self.path.chmod(0o700);before=self.path.stat()
        resumed=self.spawn('resume',tid);out,err=resumed.communicate(timeout=10)
        self.assertEqual(resumed.returncode,0,err);state=json.loads(out);self.assertEqual(state['phase'],'needs_confirmation')
        after=self.path.stat();self.assertEqual((after.st_ino,after.st_mtime_ns,stat.S_IMODE(after.st_mode)),(before.st_ino,before.st_mtime_ns,0o700))
        self.assertEqual(self.path.read_bytes(),self.content);self.assertEqual((self.root/'keep').read_text(),'unchanged')
        with Store.open_readonly(self.db) as store:
            saved=store.load(tid);self.assertTrue(saved['outcome_unresolved']);self.assertIsNotNone(saved['pending'])
        print(json.dumps({'kind':'large_delete_process_contract','model_kind':'offline_protocol_double',
              'scenario':'recreated_target_mode_changed','file_bytes':len(self.content),'killed_pid':killed.pid,
              'kill_return_code':killed.returncode,'resumed_pid':resumed.pid,'phase':state['phase'],
              'replacement_content_mode_inode_mtime_preserved':True}),flush=True)
