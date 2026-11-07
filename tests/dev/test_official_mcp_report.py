"""Interop report honesty; no installed server, network or native execution."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class OfficialMCPReportTests(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[2]/'scripts/probe_official_mcp.py'
        spec=importlib.util.spec_from_file_location('official_probe_report_test',path)
        self.probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.probe)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.report=Path(self.tmp.name)/'report.json';self.report.write_text('{"passed":true}')

    def invoke(self,**mock):
        out=io.StringIO()
        with patch.object(sys,'argv',['probe','--node','/missing','--install','/missing','--report',str(self.report)]),patch.object(self.probe,'probe',**mock),contextlib.redirect_stdout(out):
            code=self.probe.main()
        return code,json.loads(self.report.read_text()),out.getvalue()

    def test_failed_rerun_replaces_old_success_and_does_not_echo_exception(self):
        code,report,out=self.invoke(side_effect=RuntimeError('PRIVATE_SYNTHETIC_SENTINEL'))
        self.assertEqual(code,1);self.assertFalse(report['passed']);self.assertEqual(report['phase'],'failed')
        self.assertNotIn('PRIVATE',out+json.dumps(report))

    def test_success_replaces_running_marker_and_records_completed_phase(self):
        code,report,out=self.invoke(return_value={'passed':True,'real_llm_task_success_rate':None})
        self.assertEqual(code,0);self.assertTrue(report['passed']);self.assertEqual(report['phase'],'completed')


if __name__=='__main__':unittest.main()
