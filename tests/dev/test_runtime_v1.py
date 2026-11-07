import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel, tool, final


class RuntimeV1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.store = Store(Path(self.tmp.name) / 'private' / 'tasks.sqlite')
        self.addCleanup(self.store.close)

    def test_invalid_message_missing_usage_pauses_before_another_request(self):
        malformed = {'message': {'role': 'assistant', 'content': None, 'tool_calls': []}, 'usage': None}
        model = ScriptedModel([malformed, final()])
        engine = Engine(self.store, Tools(self.root), model)
        tid = engine.create('x', Limits(max_cost_usd=1, input_price=1, output_price=1))
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'needs_confirmation')
        self.assertEqual(len(model.seen), 1)

    def test_every_response_has_usage_event_even_invalid_tool_args(self):
        model = ScriptedModel([tool('read_file', {'path': 4}), final()])
        engine = Engine(self.store, Tools(self.root), model)
        tid = engine.create('x')
        state = engine.run(tid)
        events = self.store.events(tid)
        self.assertEqual(sum(e.get('input_tokens') or 0 for e in events), state['metrics']['input_tokens'])
        self.assertEqual(len([e for e in events if e['kind'] == 'model_response']), 2)

    def test_duplicate_provider_call_id_cannot_append_twice(self):
        call = tool('append_file', {'path': 'out', 'content': 'once\n'}, call_id='same_identity')
        engine = Engine(self.store, Tools(self.root), ScriptedModel([call, call, final()]))
        tid = engine.create('Append exactly once')
        state = engine.run(tid)
        self.assertEqual((self.root / 'out').read_text(), 'once\n')
        self.assertIn(state['phase'], ('failed', 'needs_confirmation'))
        self.assertEqual(state['reason'], 'duplicate_call_id')

    def test_receipt_transient_sqlite_failure_continues_without_reexecuting_tool(self):
        model = ScriptedModel([tool('append_file', {'path': 'out', 'content': 'once\n'}), final()])
        denied = [False]

        def hook(event, state):
            if event == 'after_tool_effect' and not denied[0]:
                def authorizer(action, table, *args):
                    if action == sqlite3.SQLITE_INSERT and table == 'tasks' and not denied[0]:
                        denied[0] = True
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                self.store.db.set_authorizer(authorizer)

        engine = Engine(self.store, Tools(self.root), model, hook)
        tid = engine.create('x')
        state = engine.run(tid)
        self.assertTrue(denied[0])
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual((self.root / 'out').read_text(), 'once\n')
        self.assertEqual(len([e for e in self.store.events(tid) if e['kind'] == 'tool_finished']), 1)

    def test_storage_failure_does_not_mutate_pending_or_seal_failed(self):
        from agent_lab.store import StorageUnavailable
        model = ScriptedModel([tool('append_file', {'path': 'out', 'content': 'once\n'})])
        def hook(event, state):
            if event == 'after_tool_effect':
                self.store.db.execute('PRAGMA query_only=ON')
        engine = Engine(self.store, Tools(self.root), model, hook)
        tid = engine.create('x')
        with self.assertRaises(StorageUnavailable):
            engine.run(tid)
        self.store.db.execute('PRAGMA query_only=OFF')
        durable = self.store.load(tid)
        self.assertEqual(durable['phase'], 'tool_pending')
        self.assertEqual(durable['pending']['status'], 'running')
        resumed = Engine(self.store, Tools(self.root), ScriptedModel([])).run(tid)
        self.assertEqual(resumed['phase'], 'needs_confirmation')
        self.assertEqual((self.root / 'out').read_text(), 'once\n')

    def test_save_event_is_idempotent_under_lost_commit_ack(self):
        state = {'id': 'idempotent', '_event_id': 'unique-event', 'revision': 1}
        self.store.save(state, 'created')
        self.store.save(state, 'created')
        self.assertEqual(len(self.store.events(state['id'])), 1)

    def test_abort_keeps_completed_history(self):
        engine = Engine(self.store, Tools(self.root), ScriptedModel([final()]))
        tid = engine.create('x')
        engine.run(tid)
        self.assertEqual(engine.abort(tid)['phase'], 'completed')

    def test_unknown_phase_is_rejected_without_model_call(self):
        model = ScriptedModel([final()])
        engine = Engine(self.store, Tools(self.root), model)
        tid = engine.create('x')
        state = self.store.load(tid)
        state['phase'] = 'unknown_phase'
        self.store.save(state, 'test_invalid')
        with self.assertRaises(ValueError):
            engine.run(tid)
        self.assertEqual(model.seen, [])
