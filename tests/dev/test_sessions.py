"""Persisted user turns exercise the real engine/store and local file tools."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest

from agent_lab.engine import Engine, Limits
from agent_lab.model import ModelError
from agent_lab.store import Busy, Store, StorageUnavailable
from agent_lab.tools import Tools
from tests.support import ScriptedModel, final, tool


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.path = Path(self.tmp.name) / 'private' / 'state.sqlite'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def engine(self, responses=(), tools=None):
        return Engine(self.store, tools or Tools(self.root), ScriptedModel(responses))

    def completed(self, limits=None):
        engine = self.engine([final('first answer')])
        task_id = engine.create('first request', limits)
        return engine, engine.run(task_id)

    def test_followup_survives_reopen_preserves_full_history_and_turn_events(self):
        engine = self.engine([tool('append_file', {'path': 'out', 'content': 'one'}), final('wrote one')])
        task_id = engine.create('write one')
        first = engine.run(task_id)
        old_events = self.store.events(task_id)
        turn_id = engine.continue_turn(task_id, 'now inspect it')
        with Store(self.path) as reopened:
            model = ScriptedModel([tool('read_file', {'path': 'out'}), final('it contains one')])
            second = Engine(reopened, Tools(self.root), model).run(task_id)
            self.assertEqual(second['session_id'], first['session_id'])
            self.assertEqual(second['id'], task_id)
            self.assertEqual(second['turn_id'], turn_id)
            self.assertNotEqual(turn_id, first['turn_id'])
            self.assertEqual(model.seen[0][:-1], first['messages'])
            self.assertEqual(model.seen[0][-1], {'role': 'user', 'content': 'now inspect it'})
            self.assertEqual((self.root / 'out').read_text(), 'one')
            turns = reopened.turns(task_id)
            self.assertEqual([t['phase'] for t in turns], ['completed', 'completed'])
            self.assertEqual([t['turn_id'] for t in turns], [first['turn_id'], turn_id])
            self.assertEqual(turns[0]['message_end'], turns[1]['message_start'])
            self.assertEqual(second['metrics']['model_calls'], 2)
            self.assertEqual(second['session_metrics']['model_calls'], 4)
            self.assertEqual(second['session_metrics']['input_tokens'], 400)
            self.assertEqual(reopened.events(task_id)[:len(old_events)], old_events)
            new_events = reopened.events(task_id)[len(old_events):]
            self.assertEqual(new_events[0]['kind'], 'turn_started')
            self.assertTrue(all(e['turn_id'] == turn_id for e in new_events))
            self.assertNotIn('now inspect it', json.dumps(new_events))
            self.assertEqual(Engine(reopened, Tools(self.root), ScriptedModel([])).run(task_id), second)

    def test_each_turn_gets_fresh_step_time_and_cost_budgets_with_explicit_totals(self):
        limits = Limits(max_steps=1, max_seconds=5, max_cost_usd=.008, input_price=1, output_price=1)
        engine, first = self.completed(limits)
        self.assertEqual(first['phase'], 'completed')
        first['deadline'] = time.time() - 1
        self.store.save(first, 'test_expired')
        followup = self.engine([final('second answer')])
        followup.continue_turn(first['id'], 'second request', Limits(max_steps=1, max_cost_usd=.008, input_price=1, output_price=1))
        ready = self.store.load(first['id'])
        self.assertEqual(ready['metrics']['model_calls'], 0)
        self.assertEqual(ready['metrics']['reserved_usd'], 0)
        self.assertEqual(ready['session_metrics']['model_calls'], 1)
        self.assertGreater(ready['deadline'], time.time())
        state = followup.run(first['id'])
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['metrics']['model_calls'], 1)
        self.assertEqual(state['session_metrics']['model_calls'], 2)
        self.assertAlmostEqual(state['session_metrics']['cost_usd'], .00024)
        self.assertAlmostEqual(state['session_metrics']['reserved_usd'],
                               first['metrics']['reserved_usd'] + state['metrics']['reserved_usd'])
        self.assertGreater(state['session_metrics']['reserved_usd'], .008)

    def test_failed_turn_followup_resets_errors_retries_and_keeps_failure_record(self):
        engine = self.engine([tool('read_file', {'path': 'missing'})])
        task_id = engine.create('read missing', Limits(max_errors=1))
        failed = engine.run(task_id)
        self.assertEqual(failed['reason'], 'error_limit')
        followup = self.engine([final('revised')])
        followup.continue_turn(task_id, 'answer without reading')
        ready = self.store.load(task_id)
        self.assertEqual((ready['errors'], ready['model_retries'], ready['reason']), (0, 0, None))
        self.assertEqual(followup.run(task_id)['phase'], 'completed')
        self.assertEqual(self.store.turns(task_id)[0]['reason'], 'error_limit')

    def test_followup_recovers_from_exhausted_retries_without_reusing_unknown_usage(self):
        engine = self.engine([ModelError('rate_limited', retryable=True)])
        task_id = engine.create('retry', Limits(max_model_retries=0))
        first = engine.run(task_id)
        self.assertEqual(first['reason'], 'model_retry_limit')
        self.assertEqual(first['model_retries'], 1)
        followup = self.engine([final('recovered')])
        followup.continue_turn(task_id, 'new attempt')
        ready = self.store.load(task_id)
        self.assertEqual(ready['model_retries'], 0)
        self.assertEqual(followup.run(task_id)['phase'], 'completed')
        self.assertIsNone(self.store.load(task_id)['session_metrics']['input_tokens'])

    def test_provider_call_ids_remain_consumed_across_user_turns(self):
        append = tool('append_file', {'path': 'out', 'content': 'once'}, 'same_call')
        engine = self.engine([append, final()])
        task_id = engine.create('append')
        engine.run(task_id)
        followup = self.engine([append])
        followup.continue_turn(task_id, 'continue')
        second = followup.run(task_id)
        self.assertEqual(second['reason'], 'duplicate_call_id')
        self.assertEqual((self.root / 'out').read_text(), 'once')

    def test_resume_does_not_create_a_followup_or_reset_limits(self):
        engine = self.engine([])
        task_id = engine.create('waiting', Limits(max_seconds=.001))
        before = self.store.load(task_id)
        time.sleep(.005)
        resumed = engine.run(task_id)
        self.assertEqual(resumed['turn_id'], before['turn_id'])
        self.assertEqual(resumed['reason'], 'time_limit')
        self.assertEqual(len(self.store.turns(task_id)), 1)

    def test_followup_rejects_active_or_uncertain_turn_without_modifying_state(self):
        for phase in ('ready', 'model_pending', 'tool_pending', 'needs_confirmation'):
            with self.subTest(phase=phase):
                engine = self.engine([])
                task_id = engine.create('x')
                state = self.store.load(task_id)
                state['phase'] = phase
                self.store.save(state, 'test_state')
                before = self.store.load(task_id)
                events = self.store.events(task_id)
                with self.assertRaisesRegex(ValueError, 'followup_requires_resolved_turn'):
                    engine.continue_turn(task_id, 'new request')
                self.assertEqual(self.store.load(task_id), before)
                self.assertEqual(self.store.events(task_id), events)

    def test_followup_does_not_hide_aborted_pending_tool(self):
        engine, state = self.completed()
        state['phase'], state['reason'] = 'failed', 'user_aborted'
        state['pending'] = {'status': 'running', 'intent': {'policy': 'unsafe'}}
        self.store.save(state, 'test_state')
        with self.assertRaisesRegex(ValueError, 'followup_requires_resolved_turn'):
            engine.continue_turn(state['id'], 'try again')

    def test_abort_cannot_turn_unknown_model_outcome_into_safe_followup(self):
        engine = self.engine([ModelError('model_timeout', uncertain=True)])
        task_id = engine.create('uncertain')
        self.assertEqual(engine.run(task_id)['phase'], 'needs_confirmation')
        engine.abort(task_id)
        with self.assertRaisesRegex(ValueError, 'followup_requires_resolved_turn'):
            engine.continue_turn(task_id, 'new request')

    def test_abort_of_crashed_model_request_cannot_enable_followup(self):
        engine = self.engine()
        task_id = engine.create('request interrupted by crash')
        state = self.store.load(task_id)
        state['phase'] = 'model_pending'
        self.store.save(state, 'test_request_pending')
        engine.abort(task_id)
        with self.assertRaisesRegex(ValueError, 'followup_requires_resolved_turn'):
            engine.continue_turn(task_id, 'new request')

    def test_followup_rejects_unpaired_tool_transcript_even_without_pending(self):
        engine, state = self.completed()
        state['messages'].append(tool('append_file', {'path': 'out', 'content': 'x'})['message'])
        self.store.save(state, 'test_state')
        with self.assertRaisesRegex(ValueError, 'followup_unresolved_tool_history'):
            engine.continue_turn(state['id'], 'try again')

    def test_workspace_model_and_profile_must_match_before_followup(self):
        engine, state = self.completed()
        other = self.root.parent / 'other'
        other.mkdir()
        profile_tools = Tools(self.root)
        profile_tools.profile = 'coding'
        model_engine = self.engine()
        model_engine.model.identity = 'different_model'
        candidates = [self.engine(tools=Tools(other)), model_engine, self.engine(tools=profile_tools)]
        before = self.store.load(state['id'])
        for candidate in candidates:
            with self.subTest(engine=candidate), self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
                candidate.continue_turn(state['id'], 'next')
        self.assertEqual(self.store.load(state['id']), before)

    def test_invalid_followup_and_phase_list_raise_value_error_without_mutation(self):
        engine, state = self.completed()
        for task in ('', '   ', None, [], 'x' * 65537):
            with self.subTest(task=type(task).__name__), self.assertRaisesRegex(ValueError, 'invalid_task'):
                engine.continue_turn(state['id'], task)
        state['phase'] = []
        self.store.save(state, 'test_state')
        for operation in (lambda: engine.run(state['id']), lambda: engine.continue_turn(state['id'], 'next')):
            with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
                operation()

    def test_followup_obeys_existing_workspace_and_database_locks(self):
        engine, state = self.completed()
        before = self.store.load(state['id'])
        with self.store.lock(self.root):
            with self.assertRaises(Busy):
                engine.continue_turn(state['id'], 'next')
        self.assertEqual(self.store.load(state['id']), before)

    def test_failed_followup_commit_preserves_old_turn_and_events(self):
        engine, state = self.completed()
        before = self.store.load(state['id'])
        events = self.store.events(state['id'])
        turns = self.store.turns(state['id'])
        self.store.db.execute('PRAGMA query_only=ON')
        try:
            with self.assertRaises(StorageUnavailable):
                engine.continue_turn(state['id'], 'next')
        finally:
            self.store.db.execute('PRAGMA query_only=OFF')
        self.assertEqual(self.store.load(state['id']), before)
        self.assertEqual(self.store.events(state['id']), events)
        self.assertEqual(self.store.turns(state['id']), turns)

    def test_unknown_prior_usage_stays_unknown_in_session_totals(self):
        response = final('first')
        response['usage'] = None
        engine = self.engine([response])
        task_id = engine.create('first')
        first = engine.run(task_id)
        self.assertTrue(first['usage_unknown'])
        followup = self.engine([final('second')])
        followup.continue_turn(task_id, 'next')
        ready = self.store.load(task_id)
        self.assertFalse(ready.get('usage_unknown', False))
        state = followup.run(task_id)
        self.assertEqual(state['metrics']['input_tokens'], 100)
        self.assertIsNone(state['session_metrics']['input_tokens'])

    def test_engine_preserves_native_and_reasoning_fields_for_tools_and_final(self):
        call = tool('list_files', {}, 'native_call')
        native_call = [{'type': 'thinking', 'thinking': 'inspect', 'signature': 'signed'},
                       {'type': 'tool_use', 'id': 'native_call', 'name': 'list_files', 'input': {}}]
        native_final = [{'type': 'text', 'text': 'done'}]
        call['message'].update(native_content=copy.deepcopy(native_call), reasoning_content='inspect')
        answer = final('done')
        answer['message'].update(native_content=copy.deepcopy(native_final), reasoning_content='finished reasoning')
        engine = self.engine([call, answer])
        task_id = engine.create('inspect')
        state = engine.run(task_id)
        self.assertEqual(state['messages'][2]['native_content'], native_call)
        self.assertEqual(state['messages'][2]['reasoning_content'], 'inspect')
        self.assertEqual(state['messages'][-1]['native_content'], native_final)
        self.assertEqual(state['messages'][-1]['reasoning_content'], 'finished reasoning')
        self.assertEqual(engine.model.seen[1][2]['native_content'], native_call)
        self.assertEqual(self.store.load(task_id)['messages'], state['messages'])

    def test_legacy_snapshot_can_start_followup_without_losing_old_turn(self):
        engine, state = self.completed()
        for field in ('session_metrics', 'prior_metrics', 'turn_started_at', 'turn_completed_at', 'turn_message_start'):
            state.pop(field, None)
        self.store.db.execute('DELETE FROM turns WHERE task_id=?', (state['id'],))
        self.store.db.execute('UPDATE tasks SET state=? WHERE id=?', (json.dumps(state), state['id']))
        self.store.db.commit()
        engine.continue_turn(state['id'], 'legacy followup')
        turns = self.store.turns(state['id'])
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]['turn_id'], state['turn_id'])
        self.assertEqual(turns[0]['phase'], 'completed')
        self.assertEqual(self.store.load(state['id'])['session_metrics']['model_calls'], 1)
