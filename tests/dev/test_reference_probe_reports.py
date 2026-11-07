"""Reference evidence must distinguish natural EOF, absent observation and timeout."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import probe_reference_tool_boundary as probe


class ReferenceProbeReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        binary=Path(self.temp.name)/'synthetic-binary';binary.write_bytes(b'owned fake identity only')
        replacement=patch.object(probe,'REFERENCE_BINARY',binary);replacement.start();self.addCleanup(replacement.stop)

    def run_mocked(self,reference,harness):
        with patch.object(probe,'run_reference',return_value=reference),patch.object(probe,'run_harness',return_value=harness):
            return probe.probe(eof=True,mcp=True)

    def test_killed_or_unobserved_reference_is_not_a_successful_eof_experiment(self):
        reference={'actual_receipt':'owned-tool-receipt\n','exit_code':1,'timed_out':False,'requests':1,
            'item_done_observed':True,'stdout':'{"type":"turn.failed","error":{"message":"stream closed before response.completed"}}\n'}
        harness={'actual_receipt':None,'phase':'needs_confirmation','requests':1,'item_done_observed':True,
            'phase_before_completed':'model_pending','pending_tool_before_completed':False,'exact_approvals':0}
        for changed in ({'exit_code':-9,'timed_out':True},{'item_done_observed':False},{'stdout':'unrelated failure'},{'exit_code':2}):
            with self.subTest(changed=changed):self.assertFalse(self.run_mocked({**reference,**changed},harness)['passed'])
        self.assertFalse(self.run_mocked(reference,{**harness,'item_done_observed':False})['passed'])
        self.assertTrue(self.run_mocked(reference,harness)['passed'])

    def test_mcp_metadata_describes_actual_zero_approval_before_eof_and_external_effect(self):
        reference={'actual_receipt':'owned-tool-receipt\n','exit_code':1,'timed_out':False,'requests':1,
            'item_done_observed':True,'stdout':'{"type":"turn.failed","error":{"message":"stream closed before response.completed"}}\n'}
        harness={'actual_receipt':None,'phase':'needs_confirmation','requests':1,'item_done_observed':True,
            'phase_before_completed':'model_pending','pending_tool_before_completed':False,'exact_approvals':0}
        result=self.run_mocked(reference,harness)
        self.assertEqual(result['harness_effect_authority'],'trusted_external_mcp_process')
        self.assertEqual(result['harness_approval_count'],0)
        self.assertFalse(result['harness_effect_is_command_sandboxed'])


if __name__=='__main__':unittest.main()
