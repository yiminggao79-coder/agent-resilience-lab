"""Completed model tool batches execute serially through durable receipts."""
import copy
import json
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store, StorageUnavailable
from agent_lab.tools import Tools
from tests.support import ScriptedModel, final, tool


def batch(*calls):
    return {'message': {'role': 'assistant', 'content': None,
                        'tool_calls': [copy.deepcopy(call['message']['tool_calls'][0]) for call in calls]},
            'usage': {'input_tokens': 100, 'output_tokens': 40}}


class CommandEffectTools(CodingTools):
    """Controlled local effect at the command executor boundary; no shell/API."""
    def execute(self, intent, timeout):
        if intent['name'] == 'run_command':
            with (self.root / 'commands').open('a') as stream:
                stream.write(intent['args']['argv'][-1] + '\n')
            return {'ok': True, 'exit_code': 0}
        return super().execute(intent, timeout)


BATCH_CRASH_DRIVER = '''
import json, signal, sys
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel, tool, final
root, database, mode = sys.argv[1:4]
with Store(database) as store:
    def hook(event, state):
        if mode == 'crash' and event == 'after_tool_effect':
            print(json.dumps({'task_id': state['id']}), flush=True)
            while True: signal.pause()
    first = tool('write_file', {'path': 'first', 'content': 'once'}, 'first_call')
    first['message']['tool_calls'].extend(tool('write_file', {'path': 'second', 'content': 'later'}, 'second_call')['message']['tool_calls'])
    model = ScriptedModel([first, final()] if mode == 'crash' else [final('resumed')])
    engine = Engine(store, Tools(root), model, hook)
    task_id = engine.create('write two files', Limits(max_seconds=120)) if mode == 'crash' else sys.argv[4]
    state = engine.run(task_id)
    print(json.dumps({'phase': state['phase'], 'metrics': state['metrics'],
                      'results': [m['tool_call_id'] for m in state['messages'] if m['role'] == 'tool']}), flush=True)
'''


class ToolBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.path = Path(self.tmp.name) / 'state.sqlite'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def engine(self, responses=(), tools=None, hook=None):
        return Engine(self.store, tools or CodingTools(self.root), ScriptedModel(responses), hook=hook)

    def test_dependent_batch_prepares_after_prior_results_and_preserves_native_message(self):
        response = batch(tool('create_directory', {'path': 'pkg', 'parents': False}, 'mkdir'),
                         tool('write_file', {'path': 'pkg/a', 'content': 'actual'}, 'write'),
                         tool('read_file', {'path': 'pkg/a'}, 'read'))
        response['message'].update(reasoning_content='inspect then write', native_content=[
            {'type': 'thinking', 'thinking': 'inspect then write', 'signature': 'signed'}])
        observed = []

        def hook(event, state):
            if event == 'after_tool_effect':
                observed.append((state['pending']['call_id'], [m['tool_call_id'] for m in state['messages'] if m['role'] == 'tool']))

        engine = self.engine([response, final('done')], hook=hook)
        task_id = engine.create('create write read')
        state = engine.run(task_id)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual((self.root / 'pkg/a').read_text(), 'actual')
        self.assertEqual(observed, [('mkdir', []), ('write', ['mkdir']), ('read', ['mkdir', 'write'])])
        tools = [m for m in state['messages'] if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in tools], ['mkdir', 'write', 'read'])
        self.assertEqual(json.loads(tools[-1]['content'])['content'], 'actual')
        assistant = next(m for m in state['messages'] if m.get('tool_calls'))
        self.assertEqual(len(assistant['tool_calls']), 3)
        self.assertEqual(assistant['native_content'], response['message']['native_content'])
        self.assertEqual(assistant['reasoning_content'], 'inspect then write')
        self.assertEqual(len(engine.model.seen), 2)
        self.assertEqual([m['tool_call_id'] for m in engine.model.seen[1] if m['role'] == 'tool'], ['mkdir', 'write', 'read'])
        self.assertEqual(state['metrics']['tool_attempts'], 3)
        self.assertEqual(state.get('tool_queue'), [])

    def test_malformed_arguments_produce_one_ordered_error_then_run_remaining_calls(self):
        invalid = tool('read_file', {'path': 'missing'}, 'bad')
        invalid['message']['tool_calls'][0]['function']['arguments'] = '{broken'
        response = batch(invalid, tool('write_file', {'path': 'out', 'content': 'good'}, 'write'),
                         tool('read_file', {'path': 'out'}, 'read'))
        engine = self.engine([response, final()])
        state = engine.run(engine.create('repair arguments'))
        self.assertEqual(state['phase'], 'completed')
        feedback = [m for m in state['messages'] if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in feedback], ['bad', 'write', 'read'])
        self.assertEqual(json.loads(feedback[0]['content']), {'ok': False, 'error': 'invalid_arguments'})
        self.assertEqual((self.root / 'out').read_text(), 'good')
        self.assertEqual(state['metrics']['tool_attempts'], 2)

    def test_invalid_or_repeated_batch_ids_reject_all_before_any_effect(self):
        for invalid_id in ('', None, [], 'duplicate'):
            with self.subTest(invalid_id=invalid_id):
                response = batch(tool('append_file', {'path': 'out', 'content': 'bad'}, 'duplicate'),
                                 tool('append_file', {'path': 'out', 'content': 'bad'}, 'second'))
                response['message']['tool_calls'][1]['id'] = invalid_id
                engine = self.engine([response, final('rejected')])
                state = engine.run(engine.create('invalid batch'))
                self.assertFalse((self.root / 'out').exists())
                self.assertEqual(state['consumed_call_ids'], [])
                self.assertEqual(state['metrics']['tool_attempts'], 0)

    def test_previously_consumed_id_rejects_entire_next_batch(self):
        first = tool('append_file', {'path': 'out', 'content': 'once'}, 'old')
        response = batch(tool('append_file', {'path': 'out', 'content': 'new'}, 'fresh'),
                         tool('append_file', {'path': 'out', 'content': 'duplicate'}, 'old'))
        engine = self.engine([first, response, final()])
        state = engine.run(engine.create('do not duplicate'))
        self.assertEqual(state['reason'], 'duplicate_call_id')
        self.assertEqual((self.root / 'out').read_text(), 'once')
        self.assertEqual(state['consumed_call_ids'], ['old'])

    def test_eight_calls_allowed_ninth_rejects_whole_response(self):
        for count in (8, 9):
            with self.subTest(count=count):
                calls = [tool('write_file', {'path': 'f%s' % n, 'content': str(n)}, 'call%s' % n) for n in range(count)]
                engine = self.engine([batch(*calls), final()])
                state = engine.run(engine.create('bounded batch'))
                self.assertEqual(state['phase'], 'completed')
                self.assertEqual(state['metrics']['tool_attempts'], 8 if count == 8 else 0)
                if count == 8:
                    for n in range(8):
                        (self.root / ('f%s' % n)).unlink()

    def test_model_step_limit_does_not_cut_a_previously_accepted_batch(self):
        response = batch(tool('write_file', {'path': 'one', 'content': '1'}, 'one'),
                         tool('write_file', {'path': 'two', 'content': '2'}, 'two'))
        engine = self.engine([response])
        state = engine.run(engine.create('one model step', Limits(max_steps=1)))
        self.assertEqual(state['reason'], 'step_limit')
        self.assertEqual(state['metrics']['model_calls'], 1)
        self.assertEqual(state['metrics']['tool_attempts'], 2)
        self.assertEqual((self.root / 'two').read_text(), '2')

    def test_error_limit_records_unexecuted_queue_and_allows_explicit_followup(self):
        response = batch(tool('read_file', {'path': 'missing'}, 'bad'),
                         tool('write_file', {'path': 'never', 'content': 'no'}, 'future'))
        engine = self.engine([response])
        task_id = engine.create('error cap', Limits(max_errors=1))
        state = engine.run(task_id)
        self.assertEqual(state['reason'], 'error_limit')
        self.assertEqual([m['tool_call_id'] for m in state['messages'] if m['role'] == 'tool'], ['bad','future'])
        self.assertEqual(json.loads(state['messages'][-1]['content']), {'ok':False,'error':'not_executed','reason':'error_limit'})
        self.assertEqual(state['tool_queue'], [])
        self.assertFalse((self.root / 'never').exists())
        engine.continue_turn(task_id, 'new user turn')
        self.assertEqual(self.store.load(task_id)['phase'],'ready')
        self.assertFalse((self.root/'never').exists())

    def test_deadline_after_first_effect_records_tail_not_executed(self):
        response = batch(tool('write_file', {'path': 'one', 'content': '1'}, 'one'),
                         tool('write_file', {'path': 'never', 'content': '2'}, 'two'))

        def hook(event, state):
            if event == 'after_tool_effect':
                state['deadline'] = 0

        engine = self.engine([response], hook=hook)
        state = engine.run(engine.create('deadline'))
        self.assertEqual(state['reason'], 'time_limit')
        self.assertEqual([m['tool_call_id'] for m in state['messages'] if m['role'] == 'tool'], ['one','two'])
        self.assertTrue(json.loads(state['messages'][-2]['content'])['ok'])
        self.assertEqual(json.loads(state['messages'][-1]['content']),{'ok':False,'error':'not_executed','reason':'time_limit'})
        self.assertFalse((self.root / 'never').exists())
        self.assertEqual(state['tool_queue'], [])

    def test_each_command_waits_for_its_own_approval_and_denial_advances_tail(self):
        response = batch(tool('run_command', {'argv': ['echo', 'first'], 'cwd': '.'}, 'first'),
                         tool('run_command', {'argv': ['echo', 'second'], 'cwd': '.'}, 'second'),
                         tool('write_file', {'path': 'tail', 'content': 'done'}, 'tail'))
        engine = self.engine([response, final()], tools=CommandEffectTools(self.root))
        task_id = engine.create('two commands')
        first = engine.run(task_id)
        self.assertEqual(first['phase'], 'awaiting_approval')
        self.assertFalse((self.root / 'commands').exists())
        first_approval = first['pending']['approval']
        engine.decide_approval(task_id, first_approval['request_id'], first_approval['digest'], True)
        second = engine.run(task_id)
        self.assertEqual(second['phase'], 'awaiting_approval')
        self.assertEqual((self.root / 'commands').read_text(), 'first\n')
        self.assertFalse((self.root / 'tail').exists())
        self.assertEqual(len(engine.model.seen), 1)
        second_approval = second['pending']['approval']
        self.assertNotEqual(second_approval['request_id'], first_approval['request_id'])
        denied = engine.decide_approval(task_id, second_approval['request_id'], second_approval['digest'], False)
        self.assertEqual(engine.decide_approval(task_id, second_approval['request_id'], second_approval['digest'], False), denied)
        completed = engine.run(task_id)
        self.assertEqual(completed['phase'], 'completed')
        self.assertEqual((self.root / 'commands').read_text(), 'first\n')
        self.assertEqual((self.root / 'tail').read_text(), 'done')
        feedback = [m for m in completed['messages'] if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in feedback], ['first', 'second', 'tail'])
        self.assertEqual(json.loads(feedback[1]['content'])['error'], 'approval_denied')

    def test_abort_waiting_batch_closes_unexecuted_calls_and_allows_followup(self):
        response = batch(tool('run_command', {'argv': ['echo', 'first'], 'cwd': '.'}, 'first'),
                         tool('write_file', {'path': 'never', 'content': 'no'}, 'tail'))
        engine = self.engine([response], tools=CommandEffectTools(self.root))
        task_id = engine.create('abort')
        self.assertEqual(engine.run(task_id)['phase'], 'awaiting_approval')
        aborted = engine.abort(task_id)
        self.assertEqual(aborted['reason'], 'user_aborted')
        self.assertFalse((self.root / 'never').exists())
        self.assertEqual([m['tool_call_id'] for m in aborted['messages'] if m['role'] == 'tool'], ['first','tail'])
        self.assertTrue(all(json.loads(m['content']) == {'ok':False,'error':'not_executed','reason':'user_aborted'} for m in aborted['messages'] if m['role']=='tool'))
        self.assertEqual(aborted['tool_queue'], [])
        self.assertIsNone(aborted['pending'])
        self.assertFalse((self.root/'commands').exists())
        engine.continue_turn(task_id, 'next')

    def test_feedback_storage_failure_reconciles_first_before_executing_tail(self):
        response = batch(tool('write_file', {'path': 'one', 'content': 'once'}, 'one'),
                         tool('write_file', {'path': 'two', 'content': 'later'}, 'two'))

        def hook(event, state):
            if event == 'after_tool_effect':
                self.store.db.execute('PRAGMA query_only=ON')

        engine = self.engine([response], hook=hook)
        task_id = engine.create('two writes')
        with self.assertRaises(StorageUnavailable):
            engine.run(task_id)
        self.store.db.execute('PRAGMA query_only=OFF')
        durable = self.store.load(task_id)
        self.assertEqual(durable['pending']['status'], 'running')
        self.assertEqual([c['id'] for c in durable['tool_queue']], ['two'])
        self.assertFalse((self.root / 'two').exists())
        before = (self.root / 'one').stat().st_mtime_ns
        resumed = self.engine([final('resumed')]).run(task_id)
        self.assertEqual(resumed['phase'], 'completed')
        self.assertEqual((self.root / 'one').stat().st_mtime_ns, before)
        self.assertEqual((self.root / 'two').read_text(), 'later')
        self.assertEqual([m['tool_call_id'] for m in resumed['messages'] if m['role'] == 'tool'], ['one', 'two'])

    def test_accepted_queue_and_consumed_ids_survive_exit_before_first_preparation(self):
        response = batch(tool('write_file', {'path': 'one', 'content': '1'}, 'one'),
                         tool('write_file', {'path': 'two', 'content': '2'}, 'two'))
        engine = self.engine([response])
        task_id = engine.create('durable acceptance')
        save = self.store.save

        def exit_after_acceptance(snapshot, kind, meta=None, **kwargs):
            save(snapshot, kind, meta, **kwargs)
            if kind == 'model_finished' and snapshot['phase'] == 'tool_queued':
                raise SystemExit('simulated process exit')

        with patch.object(self.store, 'save', side_effect=exit_after_acceptance):
            with self.assertRaises(SystemExit):
                engine.run(task_id)
        durable = self.store.load(task_id)
        self.assertEqual(durable['phase'], 'tool_queued')
        self.assertEqual(durable['consumed_call_ids'], ['one', 'two'])
        self.assertEqual([c['id'] for c in durable['tool_queue']], ['one', 'two'])
        self.assertIsNone(durable['pending'])
        self.assertFalse((self.root / 'one').exists())
        self.assertEqual(self.engine([final('recovered')]).run(task_id)['phase'], 'completed')
        self.assertEqual((self.root / 'two').read_text(), '2')

    def test_expired_recovery_confirms_first_effect_without_launching_tail(self):
        response = batch(tool('write_file', {'path': 'one', 'content': 'once'}, 'one'),
                         tool('write_file', {'path': 'never', 'content': 'no'}, 'tail'))

        def hook(event, state):
            if event == 'after_tool_effect':
                self.store.db.execute('PRAGMA query_only=ON')

        engine = self.engine([response], hook=hook)
        task_id = engine.create('expired recovery')
        with self.assertRaises(StorageUnavailable):
            engine.run(task_id)
        self.store.db.execute('PRAGMA query_only=OFF')
        durable = self.store.load(task_id)
        durable['deadline'] = 0
        self.store.save(durable, 'test_expired')
        resumed = self.engine([]).run(task_id)
        self.assertEqual(resumed['reason'], 'time_limit')
        self.assertEqual([m['tool_call_id'] for m in resumed['messages'] if m['role'] == 'tool'], ['one','tail'])
        self.assertTrue(json.loads(resumed['messages'][-2]['content'])['ok'])
        self.assertEqual(json.loads(resumed['messages'][-1]['content']),{'ok':False,'error':'not_executed','reason':'time_limit'})
        self.assertFalse((self.root / 'never').exists())

    def test_uncertain_first_effect_pauses_entire_batch_without_running_tail(self):
        response = batch(tool('append_file', {'path': 'out', 'content': 'once'}, 'unsafe'),
                         tool('write_file', {'path': 'never', 'content': 'no'}, 'tail'))

        def hook(event, state):
            if event == 'after_tool_effect':
                self.store.db.execute('PRAGMA query_only=ON')

        engine = self.engine([response], hook=hook)
        task_id = engine.create('unsafe first')
        with self.assertRaises(StorageUnavailable):
            engine.run(task_id)
        self.store.db.execute('PRAGMA query_only=OFF')
        resumed = self.engine([]).run(task_id)
        self.assertEqual(resumed['reason'], 'tool_outcome_unknown')
        self.assertEqual((self.root / 'out').read_text(), 'once')
        self.assertFalse((self.root / 'never').exists())
        self.assertEqual([m for m in resumed['messages'] if m['role'] == 'tool'], [])

    def test_corrupt_queued_state_rejected_before_model_or_effect(self):
        engine = self.engine([])
        task_id = engine.create('corrupt')
        state = self.store.load(task_id)
        state.update(phase='tool_queued', tool_queue=[{'id': []}])
        self.store.save(state, 'test_corruption')
        with self.assertRaisesRegex(ValueError, 'invalid_pending_state'):
            engine.run(task_id)

    def test_pending_call_cannot_also_appear_in_durable_queue(self):
        engine = self.engine([])
        task_id = engine.create('corrupt duplicate queue')
        state = self.store.load(task_id)
        call = tool('append_file', {'path': 'out', 'content': 'once'}, 'same')['message']
        state['messages'].append(call)
        state.update(phase='tool_pending', consumed_call_ids=['same'], tool_queue=copy.deepcopy(call['tool_calls']))
        state['pending'] = {'call_id': 'same', 'status': 'planned', 'operation_id': 'a' * 32,
                            'intent': engine.tools.prepare('append_file', {'path': 'out', 'content': 'once'})}
        self.store.save(state, 'test_corruption')
        with self.assertRaisesRegex(ValueError, 'invalid_pending_state'):
            engine.run(task_id)
        self.assertFalse((self.root / 'out').exists())

    def test_legacy_single_pending_snapshot_without_queue_remains_resumable(self):
        engine = self.engine([final('resumed')])
        task_id = engine.create('legacy pending')
        state = self.store.load(task_id)
        call = tool('write_file', {'path': 'legacy', 'content': 'old'}, 'legacy')['message']
        state['messages'].append(call)
        state['phase'] = 'tool_pending'
        state.pop('tool_queue', None)
        state['pending'] = {'call_id': 'legacy', 'status': 'planned', 'operation_id': 'a' * 32,
                            'intent': engine.tools.prepare('write_file', {'path': 'legacy', 'content': 'old'})}
        self.store.save(state, 'test_legacy')
        self.assertEqual(engine.run(task_id)['phase'], 'completed')
        self.assertEqual((self.root / 'legacy').read_text(), 'old')

    def test_sigkill_first_effect_then_fresh_process_recovery_preserves_order(self):
        proc = subprocess.Popen([sys.executable, '-c', BATCH_CRASH_DRIVER, str(self.root), str(self.path), 'crash'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(15), 'batch never reached crash window')
            ready = json.loads(proc.stdout.readline())
            self.assertIn('task_id', ready)
            task_id = ready['task_id']
            self.assertFalse((self.root / 'second').exists())
            before = (self.root / 'first').stat().st_mtime_ns
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=8)
            self.assertEqual(proc.returncode, -signal.SIGKILL)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=8)
            proc.stdout.close()
            proc.stderr.close()
        resumed = subprocess.run([sys.executable, '-c', BATCH_CRASH_DRIVER, str(self.root), str(self.path), 'resume', task_id],
                                 text=True, capture_output=True, timeout=15)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        state = json.loads(resumed.stdout)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['results'], ['first_call', 'second_call'])
        self.assertEqual(state['metrics']['recoveries'], 1)
        self.assertEqual((self.root / 'first').stat().st_mtime_ns, before)
        self.assertEqual((self.root / 'second').read_text(), 'later')
