"""Reuse real Runner kill/CLI stop protocol with a real owned fork descendant."""
import time

from . import test_tool_cancel_process as baseline
from agent_lab import native_jobs


class ConfinedBackendCancelProcessTests(baseline.ToolCancelProcessTests):
    def setUp(self):
        super().setUp()
        self.env['AGENT_LAB_FIXTURE_COMMAND_BACKEND']='confined-fork'

    def assert_quiet(self,native):
        super().assert_quiet(native)
        deadline=time.monotonic()+2
        while time.monotonic()<deadline and self.running(native['descendant_pid']):
            time.sleep(.02)
        self.assertFalse(self.running(native['descendant_pid']))
        self.assertFalse((self.root/'late-child').exists())
        self.assertEqual(native_jobs.inspect_workspace(self.root,self.db)['proof_kind'],'all_native_joined')
