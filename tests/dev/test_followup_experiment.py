import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
try:
    from scripts.evaluate_followup import run_phase, recovery_unchanged
except ImportError:
    run_phase=None


class FollowupExperimentTests(unittest.TestCase):
    def test_injector_kills_only_owned_child_after_durable_effect_marker(self):
        self.assertIsNotNone(run_phase,'cross-process followup experiment missing')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);marker=root/'ready.json';effect=root/'effect.txt'
            program="import os,json,signal;from pathlib import Path;Path(%r).write_text('once');Path(%r).write_text(json.dumps({'pid':os.getpid()}));signal.pause()"%(str(effect),str(marker))
            result=run_phase([sys.executable,'-c',program],marker,5)
            self.assertTrue(result['injected'])
            self.assertEqual(result['returncode'],-signal.SIGKILL)
            self.assertEqual(effect.read_text(),'once')
            self.assertEqual(json.loads(marker.read_text())['pid'],result['pid'])

    def test_recovery_proof_requires_same_operation_and_unchanged_file_identity(self):
        self.assertIsNotNone(run_phase,'cross-process followup experiment missing')
        before={'operation_id':'op','turn_id':'turn','sha256':'a','inode':10,'mtime_ns':20}
        self.assertTrue(recovery_unchanged(before,dict(before)))
        for key,value in [('operation_id','other'),('turn_id','other'),('sha256','b'),('inode',11),('mtime_ns',21)]:
            changed=dict(before);changed[key]=value
            self.assertFalse(recovery_unchanged(before,changed))
