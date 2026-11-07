"""Fixed independent acceptance cases. All model decisions are offline fixtures."""
import csv
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from agent_lab.tools import ToolError, Tools
from tests.acceptance.protocol_fixture import ProtocolFixture, feedback, finish, function_call

REPO = Path(__file__).resolve().parents[2]


class IndependentAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='arl-independent-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'workspace'
        self.root.mkdir()
        self.database = self.base / 'private-state' / 'tasks.sqlite'
        self.store = Store(self.database)
        self.addCleanup(self.store.close)
        self.tools = Tools(self.root)

    def execute(self, decisions, task='Perform the offline acceptance task.', limits=None):
        model = ProtocolFixture(decisions)
        engine = Engine(self.store, self.tools, model)
        task_id = engine.create(task, limits or Limits())
        return engine.run(task_id), model

    def test_01_csv_report_matches_independent_data_oracle(self):
        source = 'item,amount\nalpha,7\nbeta,-3\ngamma,5.5\ndelta,0.25\n'
        (self.root / 'ledger.csv').write_text(source, encoding='utf-8')
        amounts = [Decimal(row['amount']) for row in csv.DictReader(io.StringIO(source))]
        expected = {'count': len(amounts), 'sum': float(sum(amounts)),
                    'mean': float(sum(amounts) / len(amounts))}

        def write_from_feedback(messages):
            result = feedback(messages)
            self.assertTrue(result['ok'])
            report = {key: result[key] for key in expected}
            return function_call('write_file', {'path': 'summary.json', 'content': json.dumps(report)})

        def check_readback(messages):
            self.assertEqual(json.loads(feedback(messages)['content']), expected)
            return finish('Checked the written report.')

        state, model = self.execute([
            function_call('csv_stats', {'path': 'ledger.csv', 'column': 'amount'}),
            write_from_feedback,
            function_call('read_file', {'path': 'summary.json'}), check_readback])
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(json.loads((self.root / 'summary.json').read_text()), expected)
        self.assertEqual(model.calls, 4)

    def test_02_invalid_argument_feedback_allows_repair(self):
        (self.root / 'input.txt').write_text('actual input value', encoding='utf-8')

        def repair(messages):
            result = feedback(messages)
            self.assertIs(result['ok'], False)
            self.assertIsInstance(result['error'], str)
            return function_call('read_file', {'path': 'input.txt'})

        def observe(messages):
            self.assertEqual(feedback(messages)['content'], 'actual input value')
            return finish()

        state, _ = self.execute([function_call('read_file', {'path': 19}), repair, observe])
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual((self.root / 'input.txt').read_text(), 'actual input value')

    def test_03_false_completion_does_not_establish_task_success(self):
        state, _ = self.execute([finish('I created required.json correctly.')], task='Create required.json.')
        external_oracle = (self.root / 'required.json').is_file()
        self.assertFalse(external_oracle)
        self.assertEqual(state['phase'], 'completed')
        self.assertIsNone(state['verified_success'])

    def test_04_paths_cannot_read_or_overwrite_outside_or_hidden_data(self):
        outside = self.base / 'outside.txt'
        outside.write_text('outside sentinel', encoding='utf-8')
        (self.root / '.hidden').write_text('hidden sentinel', encoding='utf-8')
        (self.root / 'shortcut').symlink_to(self.base, target_is_directory=True)
        os.mkfifo(str(self.root / 'pipe'))
        for path in ('../outside.txt', str(outside), '.hidden', 'shortcut/outside.txt', 'pipe'):
            for operation in ('read_file', 'write_file'):
                arguments = {'path': path}
                if operation == 'write_file':
                    arguments['content'] = 'must not appear'
                with self.subTest(path=path, operation=operation), self.assertRaises(ToolError):
                    intent = self.tools.prepare(operation, arguments)
                    self.tools.execute(intent, 1)
        self.assertEqual(outside.read_text(), 'outside sentinel')
        self.assertEqual((self.root / '.hidden').read_text(), 'hidden sentinel')

    def test_05_step_budget_stops_before_another_model_decision(self):
        state, model = self.execute([function_call('list_files', {}), finish()], limits=Limits(max_steps=1))
        self.assertEqual((state['phase'], state['reason']), ('failed', 'step_limit'))
        self.assertEqual(model.calls, 1)

    def test_06_missing_usage_with_cost_cap_pauses_before_mutation(self):
        response = function_call('write_file', {'path': 'unaccounted.txt', 'content': 'must not appear'})
        response['usage'] = None
        state, model = self.execute([response], limits=Limits(max_cost_usd=1, input_price=1, output_price=1))
        self.assertEqual(state['phase'], 'needs_confirmation')
        self.assertIsNone(state['metrics']['cost_usd'])
        self.assertFalse((self.root / 'unaccounted.txt').exists())
        self.assertEqual(model.calls, 1)

    def crash_at_effect(self, operation):
        command = [sys.executable, '-m', 'tests.acceptance.crash_child', str(self.root),
                   str(self.database), 'crash', operation]
        process = subprocess.Popen(command, cwd=str(REPO), text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(10), 'Child never reached requested crash window')
            line = process.stdout.readline()
            self.assertTrue(line, 'Child exited before announcing crash window')
            announcement = json.loads(line)
            self.assertEqual(announcement['window'], 'effect_before_receipt')
            self.assertEqual((self.root / 'receipt.txt').read_bytes(), b'accepted-once\n')
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=5)
            self.assertEqual(process.returncode, -signal.SIGKILL)
            return announcement['task_id']
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()

    def restart(self, task_id, operation):
        process = subprocess.run([sys.executable, '-m', 'tests.acceptance.crash_child',
                                  str(self.root), str(self.database), 'resume', operation, task_id],
                                 cwd=str(REPO), capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_07_sigkill_write_reconciles_in_a_fresh_process(self):
        task_id = self.crash_at_effect('write_file')
        before = (self.root / 'receipt.txt').stat().st_mtime_ns
        result = self.restart(task_id, 'write_file')
        self.assertEqual(result['phase'], 'completed')
        self.assertEqual((self.root / 'receipt.txt').read_bytes(), b'accepted-once\n')
        self.assertEqual((self.root / 'receipt.txt').stat().st_mtime_ns, before)
        self.assertEqual(self.restart(task_id, 'write_file')['model_calls_this_process'], 0)

    def test_08_sigkill_append_pauses_without_duplicate_bytes(self):
        task_id = self.crash_at_effect('append_file')
        for _ in range(2):
            result = self.restart(task_id, 'append_file')
            self.assertEqual(result['phase'], 'needs_confirmation')
            self.assertEqual(result['model_calls_this_process'], 0)
            self.assertEqual((self.root / 'receipt.txt').read_bytes(), b'accepted-once\n')

    def test_09_deadline_includes_time_before_resume(self):
        model = ProtocolFixture([function_call('write_file', {'path': 'late.txt', 'content': 'late'})])
        engine = Engine(self.store, self.tools, model)
        task_id = engine.create('Write before the deadline.', Limits(max_seconds=.03))
        time.sleep(.06)
        state = Engine(self.store, self.tools, model).run(task_id)
        self.assertEqual((state['phase'], state['reason']), ('failed', 'time_limit'))
        self.assertEqual(model.calls, 0)
        self.assertFalse((self.root / 'late.txt').exists())

    def test_10_public_cli_metadata_omits_private_task_and_file_data(self):
        markers = ('private_task_marker', 'private_filename.txt', 'private_content_marker', 'private_answer_marker')
        state, _ = self.execute([
            function_call('write_file', {'path': markers[1], 'content': markers[2]}),
            finish(markers[3])], task=markers[0])
        for command in ('status', 'trace'):
            process = subprocess.run([sys.executable, '-m', 'agent_lab', '--db', str(self.database),
                                      command, state['id']], cwd=str(REPO), capture_output=True,
                                     text=True, timeout=5)
            self.assertEqual(process.returncode, 0, process.stderr)
            json.loads(process.stdout)
            for marker in markers:
                self.assertNotIn(marker, process.stdout + process.stderr)
        answer = subprocess.run([sys.executable, '-m', 'agent_lab', '--db', str(self.database),
                                 'answer', state['id']], cwd=str(REPO), capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(answer.returncode, 0, answer.stderr)
        self.assertEqual(json.loads(answer.stdout), {'answer': markers[3]})


if __name__ == '__main__':
    unittest.main()
