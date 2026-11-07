"""Offline public-development grader and actual owned stdio service tests."""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from scripts import evaluate_mcp as experiment
except ImportError:
    experiment = None
from agent_lab.coding_tools import CodingTools
from agent_lab.registry import MCPToolRegistry


class MCPExperimentTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(experiment, 'MCP experiment runner missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.trial = Path(self.temp.name)
        self.root, self.expected, self.before, self.config = experiment.make_fixture(self.trial)

    def correct_candidate(self):
        policy = self.expected
        (self.root/'quote.py').write_text('def quote(amount):\n    return amount * %d + %d\n' % (policy['multiplier'], policy['offset']))
        (self.root/'settings.json').write_text(json.dumps(policy))
        (self.root/'tests'/'test_regression.py').write_text(
            'import unittest\nfrom quote import quote\nclass Regression(unittest.TestCase):\n'
            '    def test_policy(self):\n        self.assertEqual(quote(2), %d)\n' % (2*policy['multiplier']+policy['offset']))

    def state(self):
        def call(identifier, name):
            return {'role':'assistant','tool_calls':[{'id':identifier,'function':{'name':name,'arguments':'{}'}}]}
        def result(identifier, value):
            return {'role':'tool','tool_call_id':identifier,'content':json.dumps(value)}
        return {'phase':'completed','outcome_unresolved':False,'messages':[
            call('1','read_file'),result('1',{'ok':True}),
            call('2','write_file'),result('2',{'ok':True}),
            call('3','run_tests'),result('3',{'ok':True,'exit_code':0})]}

    def judge(self, state=None, source_unchanged=True, protocol=True):
        return experiment.grade_candidate(self.root, self.trial, self.expected, self.before,
            state or self.state(), source_unchanged, protocol)

    def test_model_completion_claim_cannot_pass_unmodified_project(self):
        self.assertFalse(self.judge()['task_success'])

    def test_random_policy_function_and_settings_are_verified_externally(self):
        self.correct_candidate()
        self.assertTrue(self.judge()['task_success'])
        (self.root/'quote.py').write_text('def quote(amount):\n    return amount\n')
        self.assertFalse(self.judge()['task_success'])

    def test_receipt_must_be_used_in_settings(self):
        self.correct_candidate()
        value = dict(self.expected, receipt='invented')
        (self.root/'settings.json').write_text(json.dumps(value))
        self.assertFalse(self.judge()['task_success'])

    def test_original_test_bytes_are_immutable_and_regression_file_required(self):
        self.correct_candidate()
        (self.root/'tests'/'test_regression.py').unlink()
        self.assertFalse(self.judge()['task_success'])
        self.correct_candidate()
        with (self.root/'tests'/'test_public.py').open('a') as stream:
            stream.write('\n# changed\n')
        self.assertFalse(self.judge()['task_success'])

    def test_unknown_effect_changed_runtime_and_missing_mcp_each_fail_closed(self):
        self.correct_candidate()
        for state in (dict(self.state(),phase='needs_confirmation'),dict(self.state(),outcome_unresolved=True)):
            self.assertFalse(self.judge(state)['task_success'])
        self.assertFalse(self.judge(source_unchanged=False)['task_success'])
        self.assertFalse(self.judge(protocol=False)['task_success'])

    def test_explicit_live_and_clean_runtime_gates_precede_credentials_and_ledger(self):
        with contextlib.redirect_stdout(io.StringIO()), patch.object(experiment, 'load_first_line_key', side_effect=AssertionError('credential access')), patch.object(experiment, 'BudgetLedger', side_effect=AssertionError('ledger access')):
            self.assertEqual(experiment.main([]), 2)
            with patch.object(experiment, 'implementation_snapshot', return_value={'runtime_dirty':True}):
                self.assertEqual(experiment.main(['--live']), 2)

    def test_actual_owned_stdio_contract_and_exact_scoped_approval(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':'SYNTHETIC_SENTINEL'}):
            with MCPToolRegistry(CodingTools(self.root), self.config) as registry:
                intent = experiment.fixture_intent(registry)
                self.assertTrue(intent['requires_approval'])
                self.assertTrue(experiment.authorized_intent(registry, intent, intent))
                changed = copy.deepcopy(intent)
                changed['args'] = {'other':True}
                self.assertFalse(experiment.authorized_intent(registry, changed, intent))
                result = registry.execute(intent, 3)
                self.assertEqual(result['structuredContent']['policy'], self.expected)
                self.assertEqual(result['structuredContent']['credential_env_present'],
                                 {'DEEPSEEK_API_KEY':False,'OPENAI_API_KEY':False,'ANTHROPIC_API_KEY':False})
                invalid = registry.execute(registry.prepare(intent['name'], {'other':True}), 3)
                self.assertTrue(invalid['isError'])
        methods = [json.loads(line)['method'] for line in (self.trial/'trusted'/'requests.jsonl').read_text().splitlines()]
        self.assertEqual(methods, ['initialize','notifications/initialized','tools/list','tools/call','tools/call'])


if __name__ == '__main__':
    unittest.main()
