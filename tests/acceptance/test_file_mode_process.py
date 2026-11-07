"""Actual effect-before-checkpoint SIGKILL with executable repository files."""
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


class FileModeProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.db=self.base/'private.sqlite';self.file=self.root/'receipt.txt'
        self.file.write_text('old executable content\n');self.file.chmod(0o755)

    def spawn(self,mode,tid=None):
        args=[sys.executable,'-m','tests.acceptance.crash_child',str(self.root),str(self.db),mode,'write_file']
        if tid:args.append(tid)
        p=subprocess.Popen(args,cwd=str(REPO),env={'PATH':os.defpath,'LANG':'C'},
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def cleanup():
            if p.poll() is None:p.kill()
            p.communicate(timeout=5)
        self.addCleanup(cleanup);return p

    def crash(self):
        p=self.spawn('crash')
        with selectors.DefaultSelector() as selector:
            selector.register(p.stdout,selectors.EVENT_READ)
            self.assertTrue(selector.select(10),'effect barrier not reached')
        receipt=json.loads(p.stdout.readline());tid=receipt['task_id']
        self.assertEqual(receipt['window'],'effect_before_receipt')
        self.assertEqual(self.file.read_text(),'accepted-once\n')
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode),0o755)
        with Store(self.db) as store:
            state=store.load(tid)
            self.assertEqual(state['pending']['status'],'running')
            self.assertEqual(state['pending']['intent']['before_mode'],0o755)
            self.assertEqual(state['pending']['intent']['desired_mode'],0o755)
        os.kill(p.pid,signal.SIGKILL);p.communicate(timeout=5)
        self.assertEqual(p.returncode,-signal.SIGKILL)
        return p,tid

    def test_matching_content_and_mode_reconcile_after_kill_without_rewrite(self):
        killed,tid=self.crash();before=self.file.stat()
        digest=hashlib.sha256(self.file.read_bytes()).hexdigest()
        resumed=self.spawn('resume',tid);out,err=resumed.communicate(timeout=10)
        self.assertEqual(resumed.returncode,0,err);self.assertNotEqual(killed.pid,resumed.pid)
        self.assertEqual(json.loads(out)['phase'],'completed')
        after=self.file.stat()
        self.assertEqual((after.st_ino,after.st_mtime_ns,stat.S_IMODE(after.st_mode)),
                         (before.st_ino,before.st_mtime_ns,0o755))
        self.assertEqual(hashlib.sha256(self.file.read_bytes()).hexdigest(),digest)
        print(json.dumps({'kind':'file_mode_process_contract','model_kind':'offline_protocol_double',
              'killed_pid':killed.pid,'kill_return_code':killed.returncode,'resumed_pid':resumed.pid,
              'phase':'completed','mode':oct(stat.S_IMODE(after.st_mode)),'inode_mtime_unchanged':True,
              'sha256_unchanged':True}),flush=True)

    def test_changed_mode_after_effect_is_not_silently_repaired_or_claimed_complete(self):
        killed,tid=self.crash();self.file.chmod(0o700)
        resumed=self.spawn('resume',tid);out,err=resumed.communicate(timeout=10)
        self.assertEqual(resumed.returncode,0,err)
        self.assertEqual(json.loads(out)['phase'],'needs_confirmation')
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode),0o700)
        self.assertEqual(self.file.read_text(),'accepted-once\n')
        print(json.dumps({'kind':'file_mode_process_contract','model_kind':'offline_protocol_double',
              'killed_pid':killed.pid,'kill_return_code':killed.returncode,'resumed_pid':resumed.pid,
              'phase':'needs_confirmation','external_mode_preserved':'0o700'}),flush=True)

    def test_move_destination_effect_survives_kill_then_source_is_deleted_after_mode_confirmation(self):
        from tests.dev.test_patch_text import CRASH_DRIVER,patch_text
        body=patch_text('*** Update File: receipt.txt\n*** Move to: moved.txt\n@@\n-old executable content\n+moved value')
        def spawn(mode,tid=None):
            args=[sys.executable,'-c',CRASH_DRIVER,str(self.root),str(self.db),mode,body]
            if tid:args.append(tid)
            p=subprocess.Popen(args,cwd=str(REPO),env={'PATH':os.defpath,'LANG':'C'},
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            def cleanup():
                if p.poll() is None:p.kill()
                p.communicate(timeout=5)
            self.addCleanup(cleanup);return p
        killed=spawn('crash')
        with selectors.DefaultSelector() as selector:
            selector.register(killed.stdout,selectors.EVENT_READ)
            self.assertTrue(selector.select(10),'move effect barrier not reached')
        receipt=json.loads(killed.stdout.readline());target=self.root/'moved.txt'
        self.assertTrue(self.file.exists())  # Move is an explicit two-effect prefix.
        self.assertEqual(target.read_text(),'moved value\n')
        before=target.stat();self.assertEqual(stat.S_IMODE(before.st_mode),0o755)
        os.kill(killed.pid,signal.SIGKILL);killed.communicate(timeout=5)
        self.assertEqual(killed.returncode,-signal.SIGKILL)
        resumed=spawn('resume',receipt['id']);out,err=resumed.communicate(timeout=10)
        self.assertEqual(resumed.returncode,0,err);self.assertNotEqual(killed.pid,resumed.pid)
        self.assertEqual(json.loads(out)['state']['phase'],'completed')
        after=target.stat();self.assertFalse(self.file.exists())
        self.assertEqual((after.st_ino,after.st_mtime_ns,stat.S_IMODE(after.st_mode)),
                         (before.st_ino,before.st_mtime_ns,0o755))
        print(json.dumps({'kind':'file_mode_process_contract','model_kind':'offline_protocol_double',
              'scenario':'move_destination_before_cursor','killed_pid':killed.pid,
              'kill_return_code':killed.returncode,'resumed_pid':resumed.pid,'phase':'completed',
              'destination_mode':'0o755','destination_inode_mtime_unchanged':True,
              'source_deleted_after_confirmation':True}),flush=True)
