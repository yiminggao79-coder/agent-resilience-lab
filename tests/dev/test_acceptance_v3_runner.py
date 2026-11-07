"""Only synthetic tasks; never open the frozen real V3 package for tuning."""
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.engine import Engine
from agent_lab.tools import ToolError
from tests.support import ScriptedModel,tool,final


class AcceptanceV3RunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('scripts.evaluate_acceptance_v3'),
                             'separately versioned V3 runner is missing')
        self.runner=importlib.import_module('scripts.evaluate_acceptance_v3')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.suite=self.base/'synthetic-suite';self.suite.mkdir()
        root=self.suite/'project';root.mkdir()
        (root/'solve.py').write_text('def combine(a,b):\n return a-b\n')
        (root/'test_api.py').write_text('import unittest\nfrom solve import combine\nclass T(unittest.TestCase):\n def test_it(self):\n  self.assertEqual(combine(2,3),5)\n')
        self.case={'id':'synthetic_addition','task':'PRIVATE_SYNTHETIC_TASK: repair addition',
                   'workspace_dir':'project','immutable_files':['test_api.py'],
                   'oracle':{'module':'solve','function':'combine','inputs':[[3,4],[-1,2]],'expected':[7,1]}}

    def run_case(self,model):
        return self.runner.run_case_v3(self.suite,self.case,self.base/'trial',model)

    def test_correct_code_passes_without_prescribed_model_tool_sequence(self):
        model=ScriptedModel([tool('write_file',{'path':'solve.py','content':'def combine(a,b):\n return a+b\n'},'fix'),final('done')])
        result=self.run_case(model)
        self.assertTrue(result['quality_passed'],result)
        self.assertFalse(result['confirmed_coding_cycle'])
        self.assertEqual(result['phase'],'completed')
        self.assertTrue(result['immutable_files_unchanged'])
        self.assertEqual(result['final_public_tests_exit_code'],0)
        self.assertTrue(result['oracle_matches'])
        self.assertNotIn('PRIVATE_SYNTHETIC_TASK',json.dumps(result))
        self.assertEqual(result['score_version'],'v3_quality_1')

    def test_model_self_claim_without_fix_is_measured_failure(self):
        result=self.run_case(ScriptedModel([final('All tests passed; task complete')]))
        self.assertEqual(result['phase'],'completed')
        self.assertIs(result['quality_passed'],False)
        self.assertNotEqual(result['final_public_tests_exit_code'],0)

    def test_immutable_original_test_is_host_protected_from_model_writes(self):
        model=ScriptedModel([tool('write_file',{'path':'test_api.py','content':'raise SystemExit(0)\n'},'tamper'),final('done')])
        result=self.run_case(model)
        self.assertTrue(result['immutable_files_unchanged'])
        self.assertIs(result['quality_passed'],False)
        self.assertNotEqual(result['final_public_tests_exit_code'],0)

    def test_oracle_copy_enforces_same_immutable_policy_as_final_tests(self):
        code="""from pathlib import Path
def combine(a,b):
 try:Path('test_api.py').write_text('unexpectedly mutable')
 except PermissionError:return a+b
 return -9999
"""
        model=ScriptedModel([tool('write_file',{'path':'solve.py','content':code},'fix'),final('done')])
        result=self.run_case(model)
        self.assertTrue(result['quality_passed'],result)
        self.assertTrue(result['immutable_files_unchanged'])

    def test_missing_oracle_execution_is_unmeasured_not_a_wrong_answer(self):
        model=ScriptedModel([tool('write_file',{'path':'solve.py','content':'def combine(a,b):\n return a+b\n'},'fix'),final('done')])
        with patch.object(self.runner,'observe_function',side_effect=ToolError('tool_timeout')):
            result=self.run_case(model)
        self.assertEqual(result['final_public_tests_exit_code'],0)
        self.assertIsNone(result['oracle_matches'])
        self.assertIsNone(result['quality_passed'])
        self.assertEqual(result['observation_status'],'observation_unavailable')

    def test_invalid_case_fails_before_model_or_candidate_execution(self):
        self.case['oracle']['module']='../private'
        model=ScriptedModel([])
        with self.assertRaisesRegex(ValueError,'^invalid_v3_case$'):self.run_case(model)
        self.assertEqual(model.seen,[])
        self.assertFalse((self.base/'trial').exists())

    def test_caller_can_prove_no_model_observation_and_skip_judging(self):
        with patch.object(self.runner,'observe_function',side_effect=AssertionError('no exposure')):
            result=self.runner.run_case_v3(self.suite,self.case,self.base/'trial',ScriptedModel([final()]),
                                          observation_allowed=lambda:False)
        self.assertIsNone(result['quality_passed'])
        self.assertIsNone(result['final_public_tests_exit_code'])
        self.assertEqual(result['observation_status'],'blocked_no_model_observation')

    def test_unknown_effect_never_launches_candidate_observation(self):
        def unknown(engine,tid):
            state=engine.store.load(tid)
            state.update(phase='needs_confirmation',reason='tool_outcome_unknown',outcome_unresolved=True,
                         pending={'status':'unknown','intent':{'name':'run_command','policy':'unsafe'}},tool_queue=[])
            return state
        with patch.object(Engine,'run',autospec=True,side_effect=unknown),patch.object(self.runner,'observe_function',side_effect=AssertionError('must not observe unknown candidate')):
            result=self.run_case(ScriptedModel([]))
        self.assertIsNone(result['quality_passed'])
        self.assertIsNone(result['oracle_matches'])
        self.assertIsNone(result['final_public_tests_exit_code'])
        self.assertEqual(result['observation_status'],'blocked_unresolved_execution')

    def test_failed_phase_is_observation_not_a_quality_gate_and_missing_stays_null(self):
        complete={'immutable_files_unchanged':True,'final_public_tests_exit_code':0,
                  'oracle_valid_result':True,'oracle_matches':True,'phase':'failed'}
        self.assertTrue(self.runner.grade_quality(complete))
        incomplete={**complete,'oracle_valid_result':None,'oracle_matches':None}
        self.assertIsNone(self.runner.grade_quality(incomplete))
        wrong={**complete,'oracle_matches':False}
        self.assertIs(self.runner.grade_quality(wrong),False)
        summary=self.runner.summarize_quality([{'quality_passed':True},{'quality_passed':None},{'quality_passed':False}])
        self.assertEqual(summary,{'quality_measured':2,'quality_unmeasured':1,'quality_passes':1,'quality_pass_rate':0.5})
        self.assertIsNone(self.runner.summarize_quality([{'quality_passed':None}])['quality_pass_rate'])

    def test_budget_preflight_is_readonly_and_handles_real_remaining_upper_bound(self):
        report={'cap_cny':30,'committed_upper_cny':29.410374,'unresolved_requests':6,'halted':False}
        result=self.runner.budget_preflight(report,'deepseek-flash')
        self.assertFalse(result['allowed']);self.assertEqual(result['minimum_reservation_cny'],2.129920)
        self.assertEqual(result['available_upper_cny'],0.589626)
        self.assertEqual(result['unresolved_requests'],6)
        self.assertTrue(self.runner.budget_preflight({**report,'committed_upper_cny':1},'deepseek-flash')['allowed'])
        self.assertFalse(self.runner.budget_preflight({**report,'halted':True,'committed_upper_cny':1},'deepseek-flash')['allowed'])
