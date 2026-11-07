import json
from pathlib import Path
import tempfile
import unittest
from agent_lab.engine import Engine, Limits
from agent_lab.model import ModelError
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel, tool, final


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.store = Store(Path(self.tmp.name) / 'state' / 'db.sqlite')
        self.addCleanup(self.store.close)

    def run_model(self, responses, limits=None):
        model = ScriptedModel(responses)
        engine = Engine(self.store, Tools(self.root), model)
        tid = engine.create('Process local data', limits or Limits())
        return engine.run(tid), model

    def test_multistep_real_tools_feed_actual_results_to_model(self):
        (self.root / 'data.csv').write_text('v\n4\n8\n')
        state, model = self.run_model([tool('csv_stats', {'path': 'data.csv', 'column': 'v'}),
                                      tool('write_file', {'path': 'result.json', 'content': '{"sum":12}'}), final()])
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(json.loads((self.root / 'result.json').read_text()), {'sum': 12})
        feedback = json.loads(model.seen[1][-1]['content'])
        self.assertEqual(feedback['sum'], 12)
        self.assertEqual(state['metrics']['model_calls'], 3)
        self.assertEqual(state['metrics']['tool_attempts'], 2)

    def test_invalid_tool_args_and_missing_file_can_be_corrected(self):
        state, model = self.run_model([tool('read_file', {'path': 6}), tool('read_file', {'path': 'missing'}),
                                      tool('write_file', {'path': 'ok', 'content': 'repaired'}), final()])
        self.assertEqual(state['phase'], 'completed')
        self.assertFalse(json.loads(model.seen[1][-1]['content'])['ok'])
        self.assertEqual((self.root / 'ok').read_text(), 'repaired')

    def test_repeated_tool_errors_stop(self):
        state, _ = self.run_model([tool('read_file', {'path': 'absent'}) for _ in range(4)], Limits(max_errors=2))
        self.assertEqual((state['phase'], state['reason']), ('failed', 'error_limit'))
        self.assertEqual(state['metrics']['model_calls'], 2)

    def test_step_and_cost_caps_prevent_requests(self):
        state, _ = self.run_model([tool('list_files', {})], Limits(max_steps=1))
        self.assertEqual(state['reason'], 'step_limit')
        state, model = self.run_model([final()], Limits(max_cost_usd=0.000001, input_price=1, output_price=1))
        self.assertEqual(state['reason'], 'cost_limit')
        self.assertEqual(len(model.seen), 0)

    def test_usage_is_accumulated_and_missing_is_not_zero(self):
        state, _ = self.run_model([final()], Limits(input_price=2, output_price=4))
        self.assertAlmostEqual(state['metrics']['cost_usd'], 0.00028)
        self.assertEqual(state['metrics']['input_tokens'], 100)
        response = final()
        response['usage'] = None
        state, _ = self.run_model([response])
        self.assertIsNone(state['metrics']['input_tokens'])
        self.assertIsNone(state['metrics']['cost_usd'])

    def test_retry_exhaustion_and_uncertain_request(self):
        err = ModelError('rate_limited', retryable=True)
        state, _ = self.run_model([err, err, final()], Limits(max_model_retries=1))
        self.assertEqual(state['reason'], 'model_retry_limit')
        state, _ = self.run_model([ModelError('model_timeout', uncertain=True)])
        self.assertEqual((state['phase'], state['reason']), ('needs_confirmation', 'model_timeout'))

    def test_unexpected_exception_is_safe_and_trace_does_not_leak(self):
        state, _ = self.run_model([RuntimeError('secret-api-key-and-content')])
        self.assertEqual((state['phase'], state['reason']), ('needs_confirmation', 'internal_error'))
        self.assertIsNone(state['metrics']['input_tokens'])
        self.assertIsNone(state['metrics']['cost_usd'])
        self.assertNotIn('secret-api-key-and-content', json.dumps(self.store.events(state['id'])))

    def test_completed_is_not_automatically_task_success(self):
        state, _ = self.run_model([final('I created the file successfully')])
        self.assertEqual(state['phase'], 'completed')
        self.assertFalse((self.root / 'result.json').exists())
        self.assertIsNone(state['verified_success'])

    def test_terminal_resume_does_not_repeat_model_or_tool(self):
        state, _ = self.run_model([tool('append_file', {'path': 'out', 'content': 'once'}), final()])
        engine = Engine(self.store, Tools(self.root), ScriptedModel([]))
        resumed = engine.run(state['id'])
        self.assertEqual(resumed['phase'], 'completed')
        self.assertEqual((self.root / 'out').read_text(), 'once')

    def test_wrong_workspace_or_model_cannot_resume(self):
        engine = Engine(self.store, Tools(self.root), ScriptedModel([]))
        tid = engine.create('x')
        other = self.root.parent / 'other'
        other.mkdir()
        with self.assertRaises(ValueError):
            Engine(self.store, Tools(other), ScriptedModel([])).run(tid)

    def test_invalid_limits_rejected(self):
        for kwargs in ({'max_steps': 0}, {'max_seconds': -1}, {'max_cost_usd': 1}, {'input_price': float('nan')}, {'max_errors': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Limits(**kwargs)

    def test_multi_tool_response_is_not_partially_executed(self):
        response = tool('append_file', {'path': 'out', 'content': 'x'})
        response['message']['tool_calls'] *= 2
        state, _ = self.run_model([response, final()])
        self.assertEqual(state['phase'], 'completed')
        self.assertFalse((self.root / 'out').exists())

    def test_failed_model_request_without_usage_is_not_reported_as_zero(self):
        state, _ = self.run_model([ModelError('authentication_failed')])
        self.assertIsNone(state['metrics']['input_tokens'])
        self.assertIsNone(state['metrics']['cost_usd'])

    def test_unexpected_mutating_tool_error_does_not_invite_blind_retry(self):
        class FailingTools(Tools):
            def execute(self, intent, timeout):
                from agent_lab.tools import ToolError
                super().execute(intent, timeout)
                raise ToolError('file_io_error')
        model = ScriptedModel([tool('append_file', {'path': 'out', 'content': 'once'}), final()])
        engine = Engine(self.store, FailingTools(self.root), model)
        tid = engine.create('x')
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'needs_confirmation')
        self.assertEqual((self.root / 'out').read_text(), 'once')
