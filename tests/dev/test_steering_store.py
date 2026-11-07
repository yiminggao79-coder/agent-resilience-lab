"""Durable steering inbox admission/application/finalization contracts."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from agent_lab.store import Store, StorageUnavailable


def ident():
    return uuid.uuid4().hex


class SteeringStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.sqlite'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.state = {'id': ident(), 'turn_id': ident(), 'session_id': ident(),
                      'created_at': 1, 'phase': 'ready', 'revision': 1,
                      'messages': [{'role': 'user', 'content': 'initial'}],
                      'limits': {}, 'metrics': {}}
        self.store.save(self.state, 'created')

    def queue(self, text='feedback', request_id=None, turn_id=None):
        request_id = request_id or ident()
        return self.store.queue_input(self.state['id'], turn_id or self.state['turn_id'], text, request_id)

    def candidate(self, *rows):
        candidate = copy.deepcopy(self.store.load(self.state['id']))
        candidate['revision'] += 1
        candidate['_event_id'] = ident()
        applied = []
        for row in rows:
            applied.append((row['request_id'], len(candidate['messages'])))
            candidate['messages'].append({'role': 'user', 'content': row['text']})
        return candidate, applied

    def test_queue_is_separate_durable_metadata_only_transaction(self):
        before = self.store.load(self.state['id'])
        receipt = self.queue('private content')
        self.assertEqual(receipt['status'], 'pending')
        self.assertEqual(receipt['turn_id'], self.state['turn_id'])
        self.assertEqual(self.store.load(self.state['id']), before)
        self.assertNotIn('private content', json.dumps(self.store.events(self.state['id'])))
        self.assertNotIn('text', self.store.inputs(self.state['id'])[0])
        with Store(self.path) as reopened:
            rows = reopened.inputs(self.state['id'], include_text=True)
        self.assertEqual(rows[0]['text'], 'private content')
        self.assertEqual(self.store.pending_inputs(self.state['id'], self.state['turn_id']),
                         [{'request_id': receipt['request_id'], 'text': 'private content'}])

    def test_duplicate_precedes_terminal_guard_but_conflicting_reuse_fails(self):
        rid = ident()
        receipt = self.queue('exact', rid)
        self.state['phase'] = 'completed'
        self.store.save(self.state, 'completed')
        self.assertEqual(self.queue('exact', rid), receipt)
        for text, turn in [('different', self.state['turn_id']), ('exact', ident())]:
            with self.subTest(text=text, turn=turn), self.assertRaises(ValueError):
                self.queue(text, rid, turn)
        with self.assertRaises(ValueError):
            self.queue('new')
        self.assertEqual(len([e for e in self.store.events(self.state['id']) if e['kind'] == 'input_queued']), 1)

    def test_active_phase_stale_turn_unknown_and_text_validation(self):
        for phase in ('ready', 'model_pending', 'tool_queued', 'tool_pending', 'awaiting_approval'):
            self.state['phase'] = phase
            self.store.save(self.state, 'phase_changed')
            self.assertEqual(self.queue()['status'], 'pending')
        for update in ({'phase': 'failed'}, {'phase': 'needs_confirmation'},
                       {'phase': 'ready', 'usage_unknown': True},
                       {'phase': 'ready', 'outcome_unresolved': True}):
            state = copy.deepcopy(self.state)
            state.update(update)
            self.store.save(state, 'phase_changed')
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.queue()
        self.store.save(self.state, 'phase_changed')
        with self.assertRaises(ValueError):
            self.queue(turn_id=ident())
        for text in ('', '   ', 'é' * 32769, '\ud800', None):
            with self.subTest(text=repr(text)[:40]), self.assertRaises(ValueError):
                self.queue(text)
        for rid in ('A' * 32, 'x' * 32, ident()[:-1], None):
            with self.subTest(rid=rid), self.assertRaises(ValueError):
                self.store.queue_input(self.state['id'], self.state['turn_id'], 'valid', rid)

    def test_pending_count_byte_and_lifetime_caps(self):
        receipts = [self.queue('x' * 65536) for _ in range(16)]
        with self.assertRaises(ValueError):
            self.queue('x')
        for receipt in receipts:
            self.store.withdraw_input(self.state['id'], receipt['request_id'])
        receipts = [self.queue('x') for _ in range(32)]
        with self.assertRaises(ValueError):
            self.queue('x')
        for receipt in receipts:
            self.store.withdraw_input(self.state['id'], receipt['request_id'])
        for _ in range(80):
            receipt = self.queue('x')
            self.store.withdraw_input(self.state['id'], receipt['request_id'])
        with self.assertRaises(ValueError):
            self.queue('lifetime exceeded')
        self.assertEqual(len(self.store.inputs(self.state['id'])), 128)

    def test_apply_marks_exact_user_message_and_snapshot_together(self):
        receipt = self.queue('first')
        self.queue('second')
        rows = self.store.pending_inputs(self.state['id'], self.state['turn_id'])
        candidate, applied = self.candidate(*rows)
        self.store.save(candidate, 'inputs_applied', applied_inputs=applied)
        metadata = self.store.inputs(self.state['id'])
        self.assertEqual([r['status'] for r in metadata], ['applied', 'applied'])
        self.assertEqual([r['message_index'] for r in metadata], [1, 2])
        self.assertEqual([r['applied_revision'] for r in metadata], [2, 2])
        self.assertEqual(self.store.load(self.state['id'])['messages'][-2:],
                         [{'role': 'user', 'content': 'first'}, {'role': 'user', 'content': 'second'}])
        # Exact duplicates return the latest durable status even after application.
        self.assertEqual(self.queue('first', receipt['request_id'])['status'], 'applied')

    def test_bad_application_rolls_back_all_rows_snapshot_and_event(self):
        self.queue('first')
        self.queue('second')
        rows = self.store.pending_inputs(self.state['id'], self.state['turn_id'])
        before = self.store.load(self.state['id'])
        events = self.store.events(self.state['id'])
        for change in ('wrong_text', 'old_message', 'wrong_turn', 'duplicate_id', 'missing_id'):
            candidate, applied = self.candidate(*rows)
            if change == 'wrong_text': candidate['messages'][2]['content'] = 'wrong'
            elif change == 'old_message': applied[1] = (rows[1]['request_id'], 0)
            elif change == 'wrong_turn': candidate['turn_id'] = ident()
            elif change == 'duplicate_id': applied[1] = applied[0]
            elif change == 'missing_id': applied[1] = (ident(), 2)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.save(candidate, 'inputs_applied', applied_inputs=applied)
            self.assertEqual(self.store.load(self.state['id']), before)
            self.assertEqual(self.store.events(self.state['id']), events)
            self.assertEqual([r['status'] for r in self.store.inputs(self.state['id'])], ['pending', 'pending'])

    def test_sql_failure_rolls_back_input_application_and_allows_retry(self):
        self.queue()
        rows = self.store.pending_inputs(self.state['id'], self.state['turn_id'])
        candidate, applied = self.candidate(*rows)
        with patch.object(self.store, '_save_turn', side_effect=sqlite3.OperationalError('injected')):
            with self.assertRaises(StorageUnavailable):
                self.store.save(candidate, 'inputs_applied', applied_inputs=applied)
        self.assertEqual(self.store.inputs(self.state['id'])[0]['status'], 'pending')
        self.assertEqual(self.store.load(self.state['id'])['revision'], 1)
        self.store.save(candidate, 'inputs_applied', applied_inputs=applied)
        self.assertEqual(self.store.inputs(self.state['id'])[0]['status'], 'applied')

    def test_final_gate_and_committed_event_duplicate_order(self):
        from agent_lab.store import InputsPending
        final_state = copy.deepcopy(self.state)
        final_state.update(phase='completed', revision=2, _event_id=ident())
        self.queue()
        before = self.store.load(self.state['id'])
        with self.assertRaises(InputsPending):
            self.store.save(final_state, 'completed', require_empty_inbox=True)
        self.assertEqual(self.store.load(self.state['id']), before)
        for row in self.store.inputs(self.state['id']):
            self.store.withdraw_input(self.state['id'], row['request_id'])
        self.store.save(final_state, 'completed', require_empty_inbox=True)
        with self.assertRaises(ValueError):
            self.queue('too late')
        # Retry of an already committed applied event must deduplicate before
        # guards/row-state checks, including a newly queued row.
        self.store.save(self.state, 'synthetic_reopen')
        self.queue('applied')
        rows = self.store.pending_inputs(self.state['id'], self.state['turn_id'])
        candidate, applied = self.candidate(*rows)
        self.store.save(candidate, 'inputs_applied', applied_inputs=applied)
        self.queue('later')
        count = len(self.store.events(self.state['id']))
        self.store.save(candidate, 'inputs_applied', applied_inputs=applied, require_empty_inbox=True)
        self.assertEqual(len(self.store.events(self.state['id'])), count)
        self.assertEqual(len(self.store.pending_inputs(self.state['id'], self.state['turn_id'])), 1)

    def test_withdrawal_is_idempotent_private_and_cannot_undo_applied(self):
        receipt = self.queue('private')
        self.state['phase'] = 'failed'
        self.state['outcome_unresolved'] = True
        self.store.save(self.state, 'failed')
        withdrawn = self.store.withdraw_input(self.state['id'], receipt['request_id'])
        self.assertEqual(withdrawn['status'], 'withdrawn')
        self.assertEqual(self.store.withdraw_input(self.state['id'], receipt['request_id']), withdrawn)
        self.assertEqual(self.store.pending_inputs(self.state['id'], self.state['turn_id']), [])
        self.assertNotIn('private', json.dumps(self.store.events(self.state['id'])))

    def test_withdrawal_winning_application_race_has_distinct_retry_signal(self):
        self.queue('first')
        self.queue('second')
        rows = self.store.pending_inputs(self.state['id'], self.state['turn_id'])
        candidate, applied = self.candidate(*rows)
        with Store(self.path) as sender:
            sender.withdraw_input(self.state['id'], rows[0]['request_id'])
        from agent_lab.store import InputsChanged
        with self.assertRaises(InputsChanged):
            self.store.save(candidate, 'inputs_applied', applied_inputs=applied)
        self.assertEqual(len(self.store.load(self.state['id'])['messages']), 1)
        self.assertEqual([r['status'] for r in self.store.inputs(self.state['id'])], ['withdrawn', 'pending'])
        fresh = self.store.pending_inputs(self.state['id'], self.state['turn_id'])
        candidate, applied = self.candidate(*fresh)
        self.store.save(candidate, 'inputs_applied', applied_inputs=applied)
        self.assertEqual(self.store.load(self.state['id'])['messages'][-1]['content'], 'second')

    def test_admission_and_withdrawal_event_failure_are_atomic(self):
        with patch.object(self.store, '_input_event', side_effect=sqlite3.OperationalError('injected')):
            with self.assertRaises(StorageUnavailable):
                self.queue('not committed')
        self.assertEqual(self.store.inputs(self.state['id']), [])
        receipt = self.queue('kept pending')
        with patch.object(self.store, '_input_event', side_effect=sqlite3.OperationalError('injected')):
            with self.assertRaises(StorageUnavailable):
                self.store.withdraw_input(self.state['id'], receipt['request_id'])
        self.assertEqual(self.store.inputs(self.state['id'])[0]['status'], 'pending')
        candidate, applied = self.candidate(*self.store.pending_inputs(self.state['id'], self.state['turn_id']))
        self.store.save(candidate, 'inputs_applied', applied_inputs=applied)
        with self.assertRaises(ValueError):
            self.store.withdraw_input(self.state['id'], receipt['request_id'])

    def test_final_gate_is_strict_even_with_simultaneous_application(self):
        from agent_lab.store import InputsPending
        self.queue()
        candidate, applied = self.candidate(*self.store.pending_inputs(self.state['id'], self.state['turn_id']))
        with self.assertRaises(InputsPending):
            self.store.save(candidate, 'completed', applied_inputs=applied, require_empty_inbox=True)
        self.assertEqual(self.store.inputs(self.state['id'])[0]['status'], 'pending')
        self.assertEqual(self.store.load(self.state['id'])['revision'], 1)


if __name__ == '__main__':
    unittest.main()
