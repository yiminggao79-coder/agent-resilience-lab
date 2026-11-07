"""Actual >64KiB edit effect, SIGKILL and fresh-process reconciliation."""
import hashlib
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

REPO=Path(__file__).resolve().parents[2]


class LargeEditProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.db=self.base/'private.sqlite';self.file=self.root/'large.py'
        self.original='VALUE = 1\n'+('# padding data\n'*14000)+'END = "old"\n'
        self.expected=self.original.replace('VALUE = 1','VALUE = 2').replace('END = "old"','END = "new"')
        self.file.write_text(self.original);self.file.chmod(0o755)
        self.assertGreater(self.file.stat().st_size,65536)

    def spawn(self,mode,tid=None):
        args=[sys.executable,'-m','tests.acceptance.large_edit_child',str(self.root),str(self.db),mode]
        if tid:args.append(tid)
        p=subprocess.Popen(args,cwd=str(REPO),env={'PATH':os.defpath,'LANG':'C'},text=True,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        def cleanup():
            if p.poll() is None:p.kill()
            p.communicate(timeout=5)
        self.addCleanup(cleanup);return p

    def crash(self):
        p=self.spawn('crash')
        with selectors.DefaultSelector() as selector:
            selector.register(p.stdout,selectors.EVENT_READ)
            self.assertTrue(selector.select(10),'large edit effect not reached')
        line=p.stdout.readline();self.assertTrue(line,'child ended before effect')
        receipt=json.loads(line);self.assertIn('task_id',receipt)
        self.assertEqual(self.file.read_text(),self.expected)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode),0o755)
        with Store(self.db) as store:
            pending=store.load(receipt['task_id'])['pending']
            self.assertEqual(pending['status'],'running')
            encoded=json.dumps(pending['intent'])
            self.assertLess(len(encoded),4096)
            self.assertNotIn('# padding data',encoded)
        os.kill(p.pid,signal.SIGKILL);p.communicate(timeout=5)
        self.assertEqual(p.returncode,-signal.SIGKILL)
        return p,receipt['task_id']

    def test_large_effect_matching_hash_mode_reconciles_without_rebuilding_or_rewrite(self):
        killed,tid=self.crash();before=self.file.stat()
        digest=hashlib.sha256(self.file.read_bytes()).hexdigest()
        resumed=self.spawn('resume',tid);out,err=resumed.communicate(timeout=10)
        self.assertEqual(resumed.returncode,0,err);self.assertNotEqual(killed.pid,resumed.pid)
        self.assertEqual(json.loads(out)['phase'],'completed')
        after=self.file.stat()
        self.assertEqual((after.st_ino,after.st_mtime_ns,stat.S_IMODE(after.st_mode)),
                         (before.st_ino,before.st_mtime_ns,0o755))
        self.assertEqual(hashlib.sha256(self.file.read_bytes()).hexdigest(),digest)
        with Store(self.db) as store:
            state=store.load(tid)
            messages=[json.loads(m['content']) for m in state['messages'] if m['role']=='tool']
            self.assertTrue(messages[-1]['reconciled'])
            self.assertIsNone(state['verified_success'])
        print(json.dumps({'kind':'large_edit_process_contract','model_kind':'offline_protocol_double',
              'killed_pid':killed.pid,'kill_return_code':killed.returncode,'resumed_pid':resumed.pid,
              'file_bytes':after.st_size,'phase':'completed','intent_excludes_reconstructed_content':True,
              'content_mode_inode_mtime_unchanged':True}),flush=True)

    def test_large_effect_followed_by_external_mode_change_pauses(self):
        killed,tid=self.crash();self.file.chmod(0o700)
        resumed=self.spawn('resume',tid);out,err=resumed.communicate(timeout=10)
        self.assertEqual(resumed.returncode,0,err)
        self.assertEqual(json.loads(out)['phase'],'needs_confirmation')
        self.assertEqual(self.file.read_text(),self.expected)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode),0o700)
