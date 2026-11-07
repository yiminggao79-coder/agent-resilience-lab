"""Durable cooperative stop admission/claim/observation; synthetic SQLite only."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
import uuid
from unittest.mock import patch

from agent_lab.store import Store, StorageUnavailable


def ident():
    return uuid.uuid4().hex


class StopStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = (Path(self.tmp.name) / 'work').resolve()
        self.root.mkdir()
        self.path = Path(self.tmp.name) / 'state.sqlite'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.state = {'id': ident(), 'turn_id': ident(), 'workspace': str(self.root), 'phase': 'ready',
                      'reason': None, 'created_at': 1, 'revision': 1, 'messages': [], 'limits': {}, 'metrics': {}}
        self.store.save(self.state, 'created')

    def request(self, rid=None, turn=None, workspace=None):
        return self.store.request_stop(self.state['id'], turn or self.state['turn_id'], rid or ident(),
                                       workspace or str(self.root))

    def candidate(self, phase='failed', reason='user_stop_requested'):
        candidate = copy.deepcopy(self.store.load(self.state['id']))
        candidate.update(phase=phase, reason=reason, revision=candidate['revision'] + 1, _event_id=ident())
        return candidate

    def test_request_is_queue_only_durable_metadata(self):
        before = self.store.load(self.state['id'])
        receipt = self.request()
        self.assertEqual(receipt['status'], 'requested')
        self.assertEqual(receipt['turn_id'], self.state['turn_id'])
        self.assertIsNone(receipt['observed_phase'])
        self.assertIsNone(receipt['observed_reason'])
        self.assertEqual(self.store.load(self.state['id']), before)
        with Store.open_readonly(self.path) as reopened:
            self.assertEqual(reopened.stop_request(self.state['id'], self.state['turn_id']), receipt)
        event = self.store.events(self.state['id'])[-1]
        self.assertEqual(event['kind'], 'stop_requested')
        self.assertNotIn(str(self.root), json.dumps(event))

    def test_exact_retry_terminal_and_cross_turn_id_conflict(self):
        rid = ident()
        receipt = self.request(rid)
        terminal = self.candidate()
        self.store.save(terminal, 'terminated', observed_stop=rid)
        observed = self.request(rid)
        self.assertEqual(observed['status'], 'observed')
        self.assertEqual(observed['observed_phase'], 'failed')
        self.assertEqual(observed['observed_reason'], 'user_stop_requested')
        with self.assertRaises(ValueError):
            self.request()
        next_turn = self.candidate('ready', None)
        next_turn['turn_id'] = ident()
        self.store.save(next_turn, 'new_turn')
        self.assertEqual(self.request(rid), observed)  # explicit oldTurn exact retry
        with self.assertRaises(ValueError):
            self.request(rid, next_turn['turn_id'])
        self.assertEqual(receipt['request_id'], observed['request_id'])

    def test_distinct_requests_for_one_active_turn_coalesce_without_event(self):
        first = self.request()
        second = self.request()
        self.assertEqual(first, second)
        self.assertEqual(len([e for e in self.store.events(self.state['id']) if e['kind'] == 'stop_requested']), 1)

    def test_scope_maintenance_and_terminal_admission(self):
        with self.assertRaises(ValueError):
            self.request(workspace=str(self.root.parent / 'wrong'))
        with self.assertRaises(ValueError):
            self.request(turn=ident())
        for rid in ('A' * 32, 'x' * 32, ident()[:-1]):
            with self.subTest(rid=rid), self.assertRaises(ValueError):
                self.request(rid)
        for update in [{'phase': 'completed'}, {'phase': 'needs_confirmation'},
                       {'phase': 'ready', 'turn_completed_at': 1},
                       {'phase': 'model_pending', 'model_purpose': 'semantic_compaction',
                        'last_compaction': {'origin_phase': 'completed'}}]:
            state = {**self.state, **update}
            self.store.save(state, 'synthetic_phase')
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.request()
        state = {**self.state, 'phase': 'model_pending', 'model_purpose': 'semantic_compaction',
                 'last_compaction': {'origin_phase': 'ready'}, 'usage_unknown': True, 'outcome_unresolved': True}
        self.store.save(state, 'synthetic_phase')
        self.assertEqual(self.request()['status'], 'requested')
        self.assertTrue(self.store.load(self.state['id'])['usage_unknown'])
        self.assertTrue(self.store.load(self.state['id'])['outcome_unresolved'])

    def test_stop_before_claim_blocks_writes_but_claim_before_stop_is_durable(self):
        from agent_lab.store import StopRequested
        before = self.store.load(self.state['id'])
        self.request()
        claim = self.candidate('model_pending', None)
        events = self.store.events(self.state['id'])
        with self.assertRaises(StopRequested):
            self.store.save(claim, 'model_started', require_no_stop=True)
        self.assertEqual(self.store.load(self.state['id']), before)
        self.assertEqual(self.store.events(self.state['id']), events)
        # A different Turn has no inherited stop, and its claim can win first.
        claim['turn_id'] = ident()
        self.store.save(claim, 'model_started', require_no_stop=True)
        self.request(turn=claim['turn_id'])
        self.assertEqual(self.store.load(self.state['id'])['phase'], 'model_pending')
        later_claim = self.candidate('tool_pending', None)
        with self.assertRaises(StopRequested):
            self.store.save(later_claim, 'tool_started', require_no_stop=True)

    def test_default_save_and_event_retry_stay_compatible(self):
        claim = self.candidate('model_pending', None)
        self.store.save(claim, 'model_started', require_no_stop=True)
        self.request()
        count = len(self.store.events(self.state['id']))
        self.store.save(claim, 'model_started', require_no_stop=True)
        self.assertEqual(len(self.store.events(self.state['id'])), count)
        update = self.candidate('tool_pending', None)
        self.store.save(update, 'ordinary_default_save')
        self.assertEqual(self.store.load(self.state['id'])['phase'], 'tool_pending')

    def test_observation_is_atomic_and_lost_ack_retry_deduplicates(self):
        receipt = self.request()
        state = self.candidate('needs_confirmation', 'stop_requested_unconfirmed_model')
        state['usage_unknown'] = True
        with patch.object(self.store, '_save_turn', side_effect=sqlite3.OperationalError('injected')):
            with self.assertRaises(StorageUnavailable):
                self.store.save(state, 'terminated', observed_stop=receipt['request_id'])
        self.assertEqual(self.store.stop_request(self.state['id'], self.state['turn_id'])['status'], 'requested')
        self.assertEqual(self.store.load(self.state['id'])['phase'], 'ready')
        self.store.save(state, 'terminated', observed_stop=receipt['request_id'])
        count = len(self.store.events(self.state['id']))
        self.store.save(state, 'terminated', observed_stop=receipt['request_id'])
        self.assertEqual(len(self.store.events(self.state['id'])), count)
        self.assertEqual(self.store.stop_request(self.state['id'], self.state['turn_id'])['status'], 'observed')
        self.assertTrue(self.store.load(self.state['id'])['usage_unknown'])

    def test_observation_rejects_bad_turn_request_or_nonterminal_candidate(self):
        from agent_lab.store import StopRequested
        receipt = self.request()
        for change, rid in [({'phase': 'ready'}, receipt['request_id']),
                            ({'turn_id': ident()}, receipt['request_id']), ({}, ident())]:
            candidate = self.candidate()
            candidate.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.save(candidate, 'terminated', observed_stop=rid)
        with self.assertRaises(StopRequested):
            self.store.save(self.candidate(), 'terminated', observed_stop=receipt['request_id'], require_no_stop=True)
        self.assertEqual(self.store.stop_request(self.state['id'], self.state['turn_id'])['status'], 'requested')

    def test_legacy_readonly_lookup_has_no_creation_migration_or_chmod(self):
        path = Path(self.tmp.name) / 'legacy.sqlite'
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,state TEXT)')
            connection.execute('INSERT INTO tasks VALUES(?,?)', (self.state['id'], json.dumps(self.state)))
        path.chmod(0o440)
        before = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode),
                  sorted(p.name for p in path.parent.iterdir()))
        with Store.open_readonly(path) as legacy:
            self.assertIsNone(legacy.stop_request(self.state['id'], self.state['turn_id']))
        after = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode),
                 sorted(p.name for p in path.parent.iterdir()))
        self.assertEqual(after, before)

    def test_request_event_sql_failure_rolls_back_row_and_snapshot(self):
        before = self.store.load(self.state['id'])
        events = self.store.events(self.state['id'])
        rid = ident()
        with patch.object(self.store, '_input_event', side_effect=sqlite3.OperationalError('injected')):
            with self.assertRaises(StorageUnavailable):
                self.request(rid)
        self.assertIsNone(self.store.stop_request(self.state['id'], self.state['turn_id']))
        self.assertEqual(self.store.load(self.state['id']), before)
        self.assertEqual(self.store.events(self.state['id']), events)
        receipt = self.request(rid)
        with self.assertRaises(ValueError):
            self.request(rid, workspace=str(self.root.parent / 'wrong'))
        self.assertEqual(self.store.stop_request(self.state['id'], self.state['turn_id']), receipt)


if __name__ == '__main__':
    unittest.main()
