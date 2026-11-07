import hashlib
import json
from pathlib import Path
import tempfile
import unittest
try:
    from scripts.evaluate_acceptance import verify_freeze, json_equal, run_case
except ImportError:
    verify_freeze=None
from tests.support import ScriptedModel,final


class AcceptanceRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_freeze_rejects_modified_evidence_before_model_use(self):
        self.assertIsNotNone(verify_freeze,'generic acceptance runner missing')
        suite=self.root/'suite';suite.mkdir()
        artifact=suite/'data.txt';artifact.write_text('frozen')
        (suite/'FREEZE.json').write_text(json.dumps({'files':{'data.txt':hashlib.sha256(b'frozen').hexdigest()}}))
        verify_freeze(suite)
        artifact.write_text('modified')
        with self.assertRaises(ValueError):verify_freeze(suite)

    def test_external_json_equality_does_not_accept_boolean_as_number(self):
        self.assertIsNotNone(verify_freeze,'generic acceptance runner missing')
        self.assertFalse(json_equal({'x':[1]}, {'x':[True]}))
        self.assertFalse(json_equal([1,2],[1]))
        self.assertTrue(json_equal({'x':[1,2.0]}, {'x':[1.0,2]}))

    def test_model_claim_cannot_pass_frozen_external_oracle(self):
        self.assertIsNotNone(verify_freeze,'generic acceptance runner missing')
        suite=self.root/'suite';workspace=suite/'workspace';(workspace/'tests').mkdir(parents=True)
        (workspace/'calc.py').write_text('def score():\n return 0\n')
        (workspace/'tests'/'test_public.py').write_text('import unittest\nfrom calc import score\nclass T(unittest.TestCase):\n def test_score(self): self.assertEqual(score(),1)\n')
        case={'id':'component_test','workspace_dir':'workspace','task':'Repair the project.',
              'immutable_files':['tests/test_public.py'],'oracle':{'module':'calc','function':'score','inputs':[[]],'expected':[1]}}
        result=run_case(suite,case,self.root/'trial',ScriptedModel([final('All fixed')]))
        self.assertFalse(result['task_success'])
        self.assertFalse(result['oracle_matches'])
        self.assertTrue(result['immutable_files_unchanged'])

    def test_freeze_rejects_added_candidate_file(self):
        suite=self.root/'suite';suite.mkdir()
        (suite/'original.py').write_text('x=1\n')
        (suite/'FREEZE.json').write_text(json.dumps({'files':{'original.py':hashlib.sha256(b'x=1\n').hexdigest()}}))
        verify_freeze(suite)
        (suite/'extra.py').write_text('unexpected=1\n')
        with self.assertRaises(ValueError):verify_freeze(suite)

    def test_implementation_snapshot_covers_imported_scoring_scripts(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from scripts import evaluate_acceptance as runner
        root=self.root/'implementation';(root/'scripts').mkdir(parents=True);(root/'agent_lab').mkdir()
        entry=root/'scripts'/'evaluate_acceptance.py';entry.write_text('# entry\n')
        helper=root/'scripts'/'deepseek_live.py';helper.write_text('# original helper\n')
        (root/'agent_lab'/'engine.py').write_text('# engine\n')
        def git(cmd,**kwargs):
            return SimpleNamespace(stdout='baseline\n' if 'rev-parse' in cmd else '')
        with patch.object(runner,'REPO',root),patch.object(runner,'__file__',str(entry)),patch.object(runner.subprocess,'run',git):
            before=runner.implementation_snapshot()
            helper.write_text('# modified scoring helper\n')
            after=runner.implementation_snapshot()
        self.assertNotEqual(before['source_sha256'],after['source_sha256'])

    def test_unknown_execution_state_is_not_reported_as_observed_wrong_answer(self):
        from agent_lab.model import ModelError
        suite=self.root/'unknown';workspace=suite/'workspace';(workspace/'tests').mkdir(parents=True)
        (workspace/'calc.py').write_text('def score():\n return 0\n')
        (workspace/'tests'/'test_public.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_fail(self): self.fail("baseline")\n')
        case={'id':'unknown','workspace_dir':'workspace','task':'Repair.', 'immutable_files':['tests/test_public.py'],
              'oracle':{'module':'calc','function':'score','inputs':[[]],'expected':[1]}}
        result=run_case(suite,case,self.root/'unknown-trial',ScriptedModel([ModelError('transport_unknown',uncertain=True)]))
        self.assertEqual(result['phase'],'needs_confirmation')
        self.assertIsNone(result['oracle_matches'])
        self.assertIsNone(result['final_public_tests_exit_code'])
        self.assertFalse(result['task_success'])

    def test_root_baseline_and_final_keep_same_scope_if_candidate_adds_tests_directory(self):
        from unittest.mock import patch
        from scripts import evaluate_acceptance as runner
        from tests.support import tool
        suite=self.root/'root-suite';workspace=suite/'workspace';workspace.mkdir(parents=True)
        (workspace/'calc.py').write_text('def score():\n return 0\n')
        (workspace/'test_public.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_fail(self): self.fail("root baseline")\n')
        case={'id':'scope','workspace_dir':'workspace','task':'Work.', 'immutable_files':['test_public.py'],
              'oracle':{'module':'calc','function':'score','inputs':[[]],'expected':[1]}}
        with patch.object(runner,'run_tests',wraps=runner.run_tests) as tests:
            result=run_case(suite,case,self.root/'scope-trial',ScriptedModel([
                tool('create_directory',{'path':'tests','parents':False}),final('Done')]))
        self.assertEqual(tests.call_count,2)
        self.assertEqual([call.kwargs['start_dir'] for call in tests.call_args_list],['.','.'])
        self.assertEqual(result['final_public_tests_exit_code'],1)
        self.assertFalse(result['task_success'])
