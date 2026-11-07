"""Resolved history forks preserve prefixes, not files or execution authority."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import compaction
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.store import Busy, Store, StorageUnavailable
from agent_lab.tools import Tools
from tests.support import ScriptedModel, final, tool


def history_hash(messages):
    return hashlib.sha256(json.dumps(messages, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


class SessionForkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = (Path(self.tmp.name) / 'work').resolve()
        self.root.mkdir()
        self.store = Store(Path(self.tmp.name) / 'state.sqlite')
        self.addCleanup(self.store.close)
        self.tools = Tools(self.root)

    def source(self, tools=None, responses=None, limits=None):
        engine = Engine(self.store, tools or self.tools, ScriptedModel(responses or [final('original answer')]))
        task_id = engine.create('original task', limits)
        return engine.run(task_id)

    def fork(self, source, goal='alternative goal', tools=None, limits=None):
        return Engine(self.store, tools or self.tools, None).fork_session(source['id'], goal, limits)

    def test_completed_fork_copies_exact_prefix_lineage_and_own_zero_counters(self):
        source = self.source(responses=[tool('append_file', {'path': 'shared', 'content': 'original'}, 'old_call'), final()])
        before, events = self.store.load(source['id']), self.store.events(source['id'])
        with patch.object(self.tools, 'execute', side_effect=AssertionError('fork must not execute tools')):
            child_id = self.fork(source)
        child = self.store.load(child_id)
        for key in ('id', 'session_id', 'turn_id'):
            self.assertNotEqual(child[key], source[key])
        self.assertEqual(child['messages'][:len(source['messages'])], source['messages'])
        origin = child['fork_origin']
        self.assertEqual(origin['source_task_id'], source['id'])
        self.assertEqual(origin['source_session_id'], source['session_id'])
        self.assertEqual(origin['source_turn_id'], source['turn_id'])
        self.assertEqual(origin['source_revision'], source['revision'])
        self.assertEqual(origin['message_count'], len(source['messages']))
        self.assertEqual(origin['history_sha256'], history_hash(source['messages']))
        self.assertEqual(child['consumed_call_ids'], ['old_call'])
        self.assertEqual(child['messages'][-1], {'role': 'user', 'content': 'alternative goal'})
        notice = child['messages'][-2]
        self.assertEqual(notice['_runtime_control'], 'session_fork')
        self.assertIn('shared', notice['content'].lower())
        self.assertIn('not restored', notice['content'].lower())
        self.assertEqual(child['phase'], 'ready')
        self.assertNotIn('original_bytes', self.store.events(child_id)[0])
        self.assertEqual(child['metrics']['model_calls'], 0)
        self.assertEqual(child['metrics']['input_tokens'], 0)
        self.assertEqual(child['prior_metrics'], {})
        self.assertIsNone(child['pending'])
        self.assertEqual(child['tool_queue'], [])
        self.assertEqual((self.root / 'shared').read_text(), 'original')
        self.assertEqual(self.store.load(source['id']), before)
        self.assertEqual(self.store.events(source['id']), events)

    def test_failed_resolved_fork_drops_authority_and_preserves_unknown_ancestor_totals(self):
        source = self.source()
        source.update(phase='failed', reason='step_limit', approval_decisions={'old': {'approved': True}},
                      current_plan={'plan': [{'step': 'old plan', 'status': 'in_progress'}]},
                      last_tool_cancellation={'local_quiescent': True}, auto_compaction={'attempts': 1})
        source['session_metrics']['input_tokens'] = None
        self.store.save(source, 'synthetic_source')
        child = self.store.load(self.fork(source))
        for key in ('approval_decisions', 'current_plan', 'last_tool_cancellation', 'auto_compaction', 'last_compaction'):
            self.assertNotIn(key, child)
        self.assertEqual(child['metrics']['input_tokens'], 0)
        self.assertTrue(child['fork_origin']['source_usage_unknown'])
        self.assertIsNone(self.store.load(source['id'])['session_metrics']['input_tokens'])
        self.assertIsNone(self.store.stop_request(child['id'], child['turn_id']))
        self.assertEqual(self.store.inputs(child['id']), [])

    def test_active_pending_unknown_inbox_and_bad_pairing_rejected_without_children(self):
        source = self.source()
        baseline = self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0]
        variants = [dict(phase='ready'), dict(phase='needs_confirmation'), dict(pending={'status': 'running'}),
                    dict(tool_queue=[{}]), dict(outcome_unresolved=True), dict(usage_unknown=True)]
        for variant in variants:
            state = {**copy.deepcopy(source), **variant}
            self.store.save(state, 'synthetic_source')
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                self.fork(source)
        self.store.save(source, 'synthetic_source')
        # A pending input accepted while active is retained if the task fails.
        active = copy.deepcopy(source); active.update(phase='ready', turn_completed_at=None)
        self.store.save(active, 'synthetic_source')
        receipt = self.store.queue_input(source['id'], source['turn_id'], 'unapplied input', 'a' * 32)
        self.store.save(source, 'synthetic_source')
        with self.assertRaises(ValueError): self.fork(source)
        self.store.withdraw_input(source['id'], receipt['request_id'])
        bad = copy.deepcopy(source)
        bad['messages'].append(tool('append_file', {'path': 'shared', 'content': 'bad'}, 'missing_result')['message'])
        self.store.save(bad, 'synthetic_source')
        with self.assertRaises(ValueError): self.fork(source)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0], baseline)

    def test_wrong_workspace_preflight_creates_no_control_files_and_readonly_profile_match(self):
        source = self.source()
        wrong = self.root.parent / 'wrong'; wrong.mkdir()
        with self.assertRaises(ValueError): self.fork(source, tools=Tools(wrong))
        self.assertEqual(list(wrong.iterdir()), [])
        with self.assertRaises(ValueError): self.fork(source, tools=CodingTools(self.root))
        protected = CodingTools(self.root, read_only_paths=['protected'])
        coded = self.source(tools=protected)
        with self.assertRaises(ValueError): self.fork(coded, tools=CodingTools(self.root))
        child = self.store.load(self.fork(coded, tools=protected))
        self.assertEqual(child['read_only_policy'], coded['read_only_policy'])
        self.assertEqual(child['read_only_policy_identity'], coded['read_only_policy_identity'])

    def test_semantic_prefix_and_refreshed_guidance_are_preserved_without_mutation(self):
        (self.root / 'AGENTS.md').write_text('original guidance')
        coded_tools = CodingTools(self.root)
        source = self.source(tools=coded_tools)
        source['semantic_context'] = compaction.completed_record(source['messages'], len(source['messages']),
            {'message': {'role': 'assistant', 'content': 'fallible original summary'}, 'finish_reason': 'stop'}, source['model_identity'])
        self.store.save(source, 'synthetic_semantic')
        (self.root / 'AGENTS.md').write_text('new current guidance')
        child = self.store.load(self.fork(source, tools=coded_tools))
        self.assertEqual(child['messages'][:len(source['messages'])], source['messages'])
        self.assertEqual(child['semantic_context'], source['semantic_context'])
        compaction.validate_record(child['messages'], child['semantic_context'], child['model_identity'])
        generated = [message for message in child['messages'][len(source['messages']):] if '_project_context' in message]
        self.assertEqual(len(generated), 1)
        self.assertIn('new current guidance', generated[0]['content'])
        self.assertEqual(self.store.load(source['id'])['messages'], source['messages'])

    def test_toolset_manifest_is_preserved_without_constructing_mcp_and_replay_is_blocked(self):
        source = self.source(responses=[tool('append_file', {'path': 'shared', 'content': 'once'}, 'used_call'), final()])
        child_id = self.fork(source)
        replay = Engine(self.store, self.tools, ScriptedModel([tool('append_file', {'path': 'shared', 'content': 'again'}, 'used_call')]))
        state = replay.run(child_id)
        self.assertEqual(state['reason'], 'duplicate_call_id')
        self.assertEqual((self.root / 'shared').read_text(), 'once')
        source['toolset_identity'], source['toolset_manifest'] = 'synthetic_manifest', {'version': 1, 'servers': []}
        self.store.save(source, 'synthetic_manifest')
        child = self.store.load(self.fork(source))
        self.assertEqual(child['toolset_identity'], 'synthetic_manifest')
        self.assertEqual(child['toolset_manifest'], source['toolset_manifest'])
        with self.assertRaises(ValueError):
            Engine(self.store, self.tools, ScriptedModel([])).run(child['id'])

    def test_corrupt_semantic_and_storage_failure_do_not_create_partial_child(self):
        source = self.source()
        baseline = self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0]
        bad = copy.deepcopy(source); bad['semantic_context'] = {'version': 1}
        self.store.save(bad, 'synthetic_source')
        with self.assertRaises(ValueError): self.fork(source)
        self.store.save(source, 'synthetic_source')
        with patch.object(self.store, 'save', side_effect=StorageUnavailable('injected')):
            with self.assertRaises(StorageUnavailable): self.fork(source)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0], baseline)
        with self.store.lock(self.root):
            with self.assertRaises(Busy): self.fork(source)

    def test_each_explicit_fork_is_fresh_and_overridden_limits_apply_to_child_only(self):
        source = self.source(limits=Limits(max_steps=2))
        first = self.store.load(self.fork(source, limits=Limits(max_steps=7)))
        second = self.store.load(self.fork(source))
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(first['limits']['max_steps'], 7)
        self.assertEqual(second['limits']['max_steps'], 2)
        self.assertEqual(self.store.load(source['id'])['limits']['max_steps'], 2)

    def test_known_tokens_without_usd_pricing_are_not_mislabeled_unknown_usage(self):
        source = self.source()
        self.assertIsNone(source['session_metrics']['cost_usd'])
        child = self.store.load(self.fork(source))
        self.assertFalse(child['fork_origin']['source_usage_unknown'])
        self.assertTrue(child['fork_origin']['source_cost_unknown'])

    def test_legacy_guidance_refresh_does_not_annotate_copied_prefix(self):
        (self.root / 'AGENTS.md').write_text('current guidance')
        coding = CodingTools(self.root)
        source = self.source(tools=coding)
        source['messages'].insert(1, {'role': 'system', 'content': 'Project instructions from legacy source'})
        source.pop('instruction_context_index', None)
        source.pop('instruction_fingerprint', None)
        self.store.save(source, 'synthetic_legacy')
        child = self.store.load(self.fork(source, tools=coding))
        self.assertEqual(child['messages'][:len(source['messages'])], source['messages'])
        self.assertNotIn('_project_context', child['messages'][1])
        self.assertTrue(any('_project_context' in message for message in child['messages'][len(source['messages']):]))

    def test_out_of_order_results_and_source_id_mismatch_are_rejected(self):
        source = self.source()
        bad = copy.deepcopy(source)
        batch = tool('read_file', {'path': 'a'}, 'call_a')['message']
        batch['tool_calls'].extend(tool('read_file', {'path': 'b'}, 'call_b')['message']['tool_calls'])
        bad['messages'].extend([batch, {'role': 'tool', 'tool_call_id': 'call_b', 'content': '{}'},
                                {'role': 'tool', 'tool_call_id': 'call_a', 'content': '{}'}])
        bad['consumed_call_ids'].extend(['call_a', 'call_b'])
        self.store.save(bad, 'synthetic_bad_order')
        with self.assertRaises(ValueError): self.fork(source)
        bad = copy.deepcopy(source); bad['id'] = 'b' * 32
        self.store.db.execute('UPDATE tasks SET state=? WHERE id=?', (json.dumps(bad), source['id']))
        self.store.db.commit()
        with self.assertRaises(ValueError): self.fork(source)

    def test_lost_child_commit_ack_retries_same_event_without_duplicate_child(self):
        source = self.source()
        original, failed = self.store.save, []
        def save(state, kind, meta=None, **options):
            original(state, kind, meta, **options)
            if kind == 'session_forked' and not failed:
                failed.append(state['id'])
                raise StorageUnavailable('lost_ack')
        with patch.object(self.store, 'save', side_effect=save):
            child = self.store.load(self.fork(source))
        self.assertEqual(child['id'], failed[0])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 2)
        self.assertEqual(len(self.store.events(child['id'])), 1)


if __name__ == '__main__':
    unittest.main()
