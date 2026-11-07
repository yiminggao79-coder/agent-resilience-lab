"""Task plans are durable model-authored checklists, never execution authority."""
import json
from pathlib import Path
import tempfile
import unittest

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, public_status
from agent_lab.store import Store, StorageUnavailable
from agent_lab.tools import Tools
from tests.support import ScriptedModel, final, tool


class Crash(BaseException):
    pass


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.store = Store(Path(self.tmp.name) / 'state.sqlite')
        self.addCleanup(self.store.close)

    def engine(self, responses, hook=None, coding=True):
        tools = CodingTools(self.root) if coding else Tools(self.root)
        return Engine(self.store, tools, ScriptedModel(responses), hook=hook)

    @staticmethod
    def args(step='PRIVATE_PLAN_STEP', status='pending'):
        return {'explanation': 'PRIVATE_PLAN_EXPLANATION', 'plan': [{'step': step, 'status': status}]}

    def test_replacement_persists_with_revision_feedback_and_redacted_events(self):
        engine = self.engine([tool('update_plan', self.args()), tool('update_plan', self.args('second', 'in_progress')), final()])
        task_id = engine.create('Inspect the project')
        state = engine.run(task_id)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['current_plan'], {'revision': 2, 'turn_id': state['turn_id'],
            'explanation': 'PRIVATE_PLAN_EXPLANATION', 'plan': [{'step': 'second', 'status': 'in_progress'}]})
        feedback = [json.loads(m['content']) for m in state['messages'] if m['role'] == 'tool']
        self.assertEqual(feedback, [{'ok': True, 'message': 'Plan updated', 'revision': 1},
                                    {'ok': True, 'message': 'Plan updated', 'revision': 2}])
        events = self.store.events(task_id)
        updates = [e for e in events if e['kind'] == 'plan_updated']
        self.assertEqual([e['plan_revision'] for e in updates], [1, 2])
        self.assertEqual([e['steps'] for e in updates], [1, 1])
        self.assertNotIn('PRIVATE_PLAN', json.dumps(events))
        summary = public_status(state)['plan']
        self.assertEqual(summary, {'revision': 2, 'total': 1, 'pending': 0, 'in_progress': 1, 'completed': 0})
        self.assertNotIn('PRIVATE_PLAN', json.dumps(public_status(state)))
        self.assertEqual([p.name for p in self.root.iterdir() if not p.name.startswith('.')], [])

    def test_invalid_replacements_leave_prior_plan_and_return_repairable_errors(self):
        invalid = [None, {}, {'plan': None}, {'plan': {}, 'explanation': 'x'},
            {'plan': [], 'extra': True}, {'plan': [], 'explanation': None},
            {'plan': [], 'explanation': 'x' * 4097}, {'plan': [], 'explanation': 1},
            {'plan': [{'step': 'x', 'status': 'done'}]}, {'plan': [{'step': ' ', 'status': 'pending'}]},
            {'plan': [{'step': 'x' * 1025, 'status': 'pending'}]},
            {'plan': [{'step': '界' * 342, 'status': 'pending'}]},
            {'plan': [{'step': '\ud800', 'status': 'pending'}]},
            {'plan': [{'step': 'x', 'status': 'pending', 'extra': 1}]},
            {'plan': [{'step': 'x', 'status': 'in_progress'}] * 2},
            {'plan': [{'step': 'x', 'status': 'pending'}] * 33},
            {'plan': [{'step': '\x00' * 1024, 'status': 'pending'}] * 32}]
        for arguments in invalid:
            with self.subTest(arguments=repr(arguments)[:80]):
                engine = self.engine([tool('update_plan', self.args()), tool('update_plan', arguments), final()])
                task_id = engine.create('Make a plan')
                state = engine.run(task_id)
                self.assertEqual(state['phase'], 'completed')
                self.assertEqual(state.get('current_plan', {}).get('revision'), 1)
                result = json.loads([m for m in state['messages'] if m['role'] == 'tool'][-1]['content'])
                self.assertEqual(result, {'ok': False, 'error': 'invalid_arguments'})
                self.assertEqual(len([e for e in self.store.events(task_id) if e['kind'] == 'plan_updated']), 1)

    def test_clear_survives_followup_without_changing_authority_or_verified_success(self):
        first = self.engine([tool('update_plan', self.args(status='completed')), final()])
        task_id = first.create('first')
        prior = first.run(task_id)
        self.assertIsNone(prior['verified_success'])
        following = self.engine([tool('update_plan', {'plan': []}), final()])
        following.continue_turn(task_id, 'second')
        self.assertEqual(self.store.load(task_id)['current_plan'], prior['current_plan'])
        state = following.run(task_id)
        self.assertEqual(state['current_plan'], {'revision': 2, 'turn_id': state['turn_id'], 'explanation': None, 'plan': []})
        self.assertNotEqual(state['turn_id'], prior['turn_id'])
        self.assertIsNone(state['verified_success'])
        self.assertEqual(public_status(state)['plan']['total'], 0)
        self.assertNotIn('approval', public_status(state))

    def test_optional_coding_tool_accepts_legacy_snapshot_without_plan(self):
        local = self.engine([], coding=False)
        self.assertNotIn('update_plan', [s['function']['name'] for s in local._schemas()])
        engine = self.engine([final()])
        task_id = engine.create('No plan needed')
        state = engine.run(task_id)
        self.assertNotIn('current_plan', state)
        self.assertNotIn('plan', public_status(state))
        self.assertIn('update_plan', [s['function']['name'] for s in engine._schemas()])
        followup = self.engine([tool('update_plan', {'plan': [{'step': 'check', 'status': 'pending'}]}), final()])
        followup.continue_turn(task_id, 'Add a plan')
        self.assertEqual(followup.run(task_id)['current_plan']['revision'], 1)

    def test_crash_before_plan_commit_recovers_once_without_external_effect(self):
        def crash(event, state):
            if event == 'after_tool_effect' and state.get('current_plan'):
                raise Crash()
        engine = self.engine([tool('update_plan', self.args())], hook=crash)
        task_id = engine.create('Track work')
        with self.assertRaises(Crash):
            engine.run(task_id)
        saved = self.store.load(task_id)
        self.assertEqual(saved['pending']['status'], 'running')
        self.assertNotIn('current_plan', saved)
        resumed = self.engine([final()]).run(task_id)
        self.assertEqual(resumed['current_plan']['revision'], 1)
        self.assertEqual(len([e for e in self.store.events(task_id) if e['kind'] == 'plan_updated']), 1)

    def test_transaction_failure_keeps_plan_and_feedback_uncommitted(self):
        engine = self.engine([tool('update_plan', self.args())])
        task_id = engine.create('Track work')
        self.store.db.execute("CREATE TRIGGER reject_plan_event BEFORE INSERT ON events WHEN NEW.kind='plan_updated' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        with self.assertRaises(StorageUnavailable):
            engine.run(task_id)
        saved = self.store.load(task_id)
        self.assertNotIn('current_plan', saved)
        self.assertFalse(any(m['role'] == 'tool' for m in saved['messages']))
        self.assertFalse(any(e['kind'] == 'plan_updated' for e in self.store.events(task_id)))
        self.store.db.execute('DROP TRIGGER reject_plan_event')
        resumed = self.engine([final()]).run(task_id)
        self.assertEqual(resumed['current_plan']['revision'], 1)
        self.assertEqual(len([e for e in self.store.events(task_id) if e['kind'] == 'plan_updated']), 1)

    def test_lost_commit_acknowledgement_retries_same_plan_event_identity(self):
        save = self.store.save
        lost = []
        def lose_acknowledgement(state, kind, meta=None, **kwargs):
            save(state, kind, meta, **kwargs)
            if kind == 'plan_updated' and not lost:
                lost.append(True)
                raise StorageUnavailable('storage_unavailable')
        self.store.save = lose_acknowledgement
        engine = self.engine([tool('update_plan', self.args()), final()])
        task_id = engine.create('Track work')
        state = engine.run(task_id)
        self.assertEqual(state['current_plan']['revision'], 1)
        self.assertEqual(len([m for m in state['messages'] if m['role'] == 'tool']), 1)
        self.assertEqual(len([e for e in self.store.events(task_id) if e['kind'] == 'plan_updated']), 1)

    def test_resume_after_committed_update_does_not_repeat_plan(self):
        def crash_after_commit(event):
            if event['kind'] == 'plan_updated':
                raise Crash()
        engine = self.engine([tool('update_plan', self.args())])
        engine.observer = crash_after_commit
        task_id = engine.create('Track work')
        with self.assertRaises(Crash):
            engine.run(task_id)
        committed = self.store.load(task_id)
        self.assertEqual(committed['phase'], 'ready')
        self.assertIsNone(committed['pending'])
        resumed = self.engine([final()]).run(task_id)
        self.assertEqual(resumed['current_plan'], committed['current_plan'])
        self.assertEqual(len([e for e in self.store.events(task_id) if e['kind'] == 'plan_updated']), 1)

    def test_same_batch_updates_execute_in_order_with_exact_boundary_sizes(self):
        maximum = {'explanation': 'x' * 4096, 'plan': [
            {'step': 'a' * 1024, 'status': 'completed'} for _ in range(32)]}
        batch = tool('update_plan', maximum, 'first')
        batch['message']['tool_calls'] += tool('update_plan', {'plan': []}, 'second')['message']['tool_calls']
        engine = self.engine([batch, final()])
        task_id = engine.create('Track work')
        state = engine.run(task_id)
        self.assertEqual(state['current_plan']['revision'], 2)
        self.assertEqual(state['current_plan']['plan'], [])
        results = [m for m in state['messages'] if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in results], ['first', 'second'])
        self.assertEqual([json.loads(m['content'])['revision'] for m in results], [1, 2])
        self.assertEqual([e['steps'] for e in self.store.events(task_id) if e['kind'] == 'plan_updated'], [32, 0])
