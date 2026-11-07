"""Offline automatic-compaction contracts; no model semantic-quality claims."""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from agent_lab import compaction
from agent_lab.coding_tools import CodingTools
from agent_lab.context import build_context, ContextLimit
from agent_lab.engine import Engine, Limits
from agent_lab.model import ModelError
from agent_lab.store import Store, StorageUnavailable
from agent_lab.tools import Tools
from tests.dev.test_compaction import summary
from tests.dev.test_history import pair
from tests.support import ScriptedModel, final, tool


class AutoContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        (self.root / 'f.py').write_text('value = 1\n')
        self.store = Store(Path(self.tmp.name) / 'state.sqlite')
        self.addCleanup(self.store.close)

    def fixture(self, responses, enabled=True, **changes):
        model = ScriptedModel(responses)
        engine = Engine(self.store, CodingTools(self.root), model)
        tid = engine.create('Keep exact 用户 constraints; no publication.',
                            Limits(**{'max_context_bytes': 32000, **changes}))
        state = self.store.load(tid)
        state['messages'] += [{'role': 'assistant', 'content': 'old discussion ' * 500,
                               'reasoning_content': 'PRIVATE_REASONING',
                               'native_content': [{'text': 'PRIVATE_NATIVE'}]}]
        state['messages'] += pair('old', 'read_file', {'path': 'f.py'}, {'ok': True, 'content': 'old'})
        state['messages'] += pair('latest', 'read_file', {'path': 'f.py'}, {'ok': True, 'content': 'latest'})
        if enabled:
            state['limits'].update(auto_compact_bytes=1, max_auto_compactions=2)
        state['limits'].update(changes)
        self.store.save(state, 'fixture')
        return engine, tid, model, copy.deepcopy(state)

    def test_default_disabled_and_old_snapshot_has_no_extra_model_call(self):
        engine, tid, model, _ = self.fixture([final()], enabled=False)
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(len(model.seen), 1)
        self.assertNotIn('semantic_context', state)

    def test_opt_in_summary_precedes_decision_with_exact_archive_and_usage(self):
        engine, tid, model, original = self.fixture([summary(), final('done')])
        events = []
        engine.observer = events.append
        model.on_text = events.append
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['last_compaction']['status'], 'installed')
        self.assertEqual(state['messages'][:-1], original['messages'])
        self.assertEqual(state['messages'][-1]['content'], 'done')
        self.assertEqual(state['metrics']['model_calls'], 2)
        self.assertEqual(state['metrics']['input_tokens'], 200)
        self.assertEqual(state['metrics']['output_tokens'], 40)
        self.assertEqual(state['auto_compaction']['attempts'], 1)
        self.assertNotIn('PRIVATE_', json.dumps(model.seen[0]))
        self.assertNotIn('PRIVATE_', json.dumps(events))
        self.assertIn('Fallible model-written', json.dumps(model.seen[1]))
        self.assertTrue(any(m.get('content') == original['messages'][1]['content'] for m in model.seen[1]))

    def test_active_automatic_summary_accepts_feedback_without_treating_it_as_maintenance(self):
        # Applied natural feedback releases the prior completed batch and may
        # require one more summary before the next decision.
        engine, tid, model, original = self.fixture([summary(), summary(), final('feedback considered')])
        complete = model.complete
        def sampling(*args):
            if not model.seen:
                with Store(self.store.path) as sender:
                    receipt=sender.queue_input(tid, original['turn_id'], 'Preserve this new requirement.', uuid.uuid4().hex)
                    self.assertEqual(receipt['status'], 'pending')
            return complete(*args)
        with patch.object(model, 'complete', side_effect=sampling):
            state=engine.run(tid)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(len(model.seen), 3)
        self.assertIn({'role':'user','content':'Preserve this new requirement.'}, model.seen[2])
        self.assertEqual(self.store.inputs(tid)[0]['status'], 'applied')

    def test_threshold_measures_active_view_and_triggers_at_boundary(self):
        engine, tid, model, state = self.fixture([final()], enabled=False)
        _, stats = build_context(state['messages'], engine._schemas(), 32000)
        state['limits']['auto_compact_bytes'] = stats['retained_bytes'] + 1
        self.store.save(state, 'threshold')
        self.assertEqual(engine.run(tid)['phase'], 'completed')
        self.assertEqual(len(model.seen), 1)
        engine, tid, model, state = self.fixture([summary(), final()], enabled=False)
        _, stats = build_context(state['messages'], engine._schemas(), 32000)
        state['limits']['auto_compact_bytes'] = stats['retained_bytes']
        self.store.save(state, 'threshold')
        self.assertEqual(engine.run(tid)['phase'], 'completed')
        self.assertEqual(len(model.seen), 2)

    def test_large_archive_small_active_view_does_not_trigger(self):
        engine, tid, model, state = self.fixture([final()], enabled=False)
        state['messages'][4]['content'] = json.dumps({'ok': True, 'content': 'z' * 40000})
        _, stats = build_context(state['messages'], engine._schemas(), 32000)
        self.assertGreater(stats['original_bytes'], 32000)
        state['limits']['auto_compact_bytes'] = stats['retained_bytes'] + 1
        self.store.save(state, 'large_archive')
        self.assertEqual(engine.run(tid)['phase'], 'completed')
        self.assertEqual(len(model.seen), 1)

    def test_no_reduction_is_not_retried_for_same_prefix(self):
        engine, tid, model, _ = self.fixture([summary('x' * 8192), final()])
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['last_compaction']['code'], 'compaction_no_reduction')
        self.assertEqual(state['auto_compaction']['attempts'], 1)
        self.assertEqual(len(model.seen), 2)

    def test_advancing_source_is_bounded_and_allowance_resets_per_turn(self):
        engine, tid, model, _ = self.fixture([summary(), tool('read_file', {'path': 'f.py'}),
                                             summary('New finding: f.py is current.'), final(),
                                             summary('Continue the next turn.'),
                                             tool('read_file', {'path': 'f.py'}), final()])
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['auto_compaction']['attempts'], 2)
        self.assertEqual(state['semantic_context']['generation'], 2)
        engine.continue_turn(tid, 'Continue exact requirements.')
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['auto_compaction']['attempts'], 1)
        self.assertEqual(state['semantic_context']['generation'], 3)
        self.assertEqual(len(model.seen), 7)

    def test_one_attempt_cap_allows_tools_and_then_regular_decision(self):
        engine, tid, model, _ = self.fixture([summary(), tool('read_file', {'path': 'f.py'}), final()],
                                             max_auto_compactions=1)
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['metrics']['tool_attempts'], 1)
        self.assertEqual(state['auto_compaction']['attempts'], 1)
        self.assertEqual(len(model.seen), 3)

    def test_unknown_summary_and_unknown_usage_pause_without_decision(self):
        responses = [ModelError('model_timeout', uncertain=True), ModelError('rate_limited', retryable=True),
                     {**summary(), 'usage': None}]
        for response in responses:
            with self.subTest(response=str(response)):
                engine, tid, model, _ = self.fixture([response, final()])
                state = engine.run(tid)
                self.assertEqual(state['phase'], 'needs_confirmation')
                self.assertTrue(state['usage_unknown'])
                self.assertEqual(len(model.seen), 1)
                self.assertEqual(engine.run(tid)['phase'], 'needs_confirmation')
                self.assertEqual(len(model.seen), 1)

    def test_shared_budget_refusal_does_not_fallback_to_paid_decision(self):
        for code in ('budget_exhausted', 'budget_storage_failed'):
            engine, tid, model, _ = self.fixture([ModelError(code), final()])
            state = engine.run(tid)
            self.assertEqual(state['phase'], 'failed')
            self.assertEqual(state['reason'], code)
            self.assertEqual(len(model.seen), 1)

    def test_known_invalid_summary_falls_back_with_known_usage(self):
        engine, tid, model, _ = self.fixture([summary(''), final()])
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['last_compaction']['code'], 'compaction_invalid_summary')
        self.assertEqual(state['metrics']['input_tokens'], 200)
        self.assertEqual(len(model.seen), 2)

    def test_step_and_deadline_are_rechecked_after_summary(self):
        engine, tid, model, _ = self.fixture([summary(), final()], max_steps=1)
        state = engine.run(tid)
        self.assertEqual(state['reason'], 'step_limit')
        self.assertEqual(len(model.seen), 1)
        engine, tid, model, _ = self.fixture([summary(), final()])
        # Expire through the persisted deadline after summary returns.
        now = time.time()
        with patch('agent_lab.engine.time.time', side_effect=lambda: now if not model.seen else now + 1000):
            state = engine.run(tid)
        self.assertEqual(state['reason'], 'time_limit')
        self.assertEqual(len(model.seen), 1)

    def test_storage_failure_keeps_started_attempt_and_resume_never_resends(self):
        engine, tid, model, _ = self.fixture([summary(), final()])
        save = self.store.save
        def fail_install(state, kind, meta=None, **kwargs):
            if kind == 'compaction_installed':
                raise StorageUnavailable('storage_unavailable')
            return save(state, kind, meta, **kwargs)
        with patch.object(self.store, 'save', side_effect=fail_install):
            with self.assertRaises(StorageUnavailable):
                engine.run(tid)
        durable = self.store.load(tid)
        self.assertEqual(durable['phase'], 'model_pending')
        self.assertEqual(durable['auto_compaction']['attempts'], 1)
        self.assertEqual(engine.run(tid)['reason'], 'model_outcome_unknown')
        self.assertEqual(len(model.seen), 1)

    def test_run_never_acquires_recursive_store_lock(self):
        engine, tid, _, _ = self.fixture([summary(), final()])
        lock = self.store.lock
        entered = []
        @contextmanager
        def nonrecursive(*args):
            self.assertEqual(entered, [])
            entered.append(True)
            try:
                with lock(*args):
                    yield
            finally:
                entered.pop()
        with patch.object(self.store, 'lock', side_effect=nonrecursive):
            self.assertEqual(engine.run(tid)['phase'], 'completed')

    def test_limits_validate_opt_in_threshold_and_attempt_cap(self):
        self.assertIn('auto_compact_bytes', Limits.__dataclass_fields__)
        self.assertIsNone(Limits().auto_compact_bytes)
        for value in (0, -1, True, 1.5, 262145):
            with self.subTest(threshold=value), self.assertRaises(ValueError):
                Limits(auto_compact_bytes=value)
        for value in (0, -1, True, 1.5, None):
            with self.subTest(attempts=value), self.assertRaises(ValueError):
                Limits(max_auto_compactions=value)

    def test_old_view_overflow_is_rescued_or_fails_without_resend(self):
        for response, phase in ((summary(), 'completed'), (summary(''), 'failed')):
            engine, tid, model, state = self.fixture([response, final()], max_context_bytes=16000)
            with self.assertRaises(ContextLimit):
                build_context(state['messages'], engine._schemas(), 16000)
            result = engine.run(tid)
            self.assertEqual(result['phase'], phase)
            if phase == 'completed':
                self.assertEqual(result['last_compaction']['status'], 'installed')
                self.assertEqual(len(model.seen), 2)
            else:
                self.assertEqual(result['reason'], 'context_limit')
                self.assertEqual(len(model.seen), 1)

    def test_no_new_content_skips_and_oversized_summary_input_advances_fitting_prefixes(self):
        engine, tid, model, state = self.fixture([final()])
        state['messages'] = state['messages'][:2]
        self.store.save(state, 'no_assistant_content')
        self.assertEqual(engine.run(tid)['phase'], 'completed')
        self.assertEqual(len(model.seen), 1)
        engine, tid, model, state = self.fixture([summary(), summary(), final()])
        for index in range(20):
            state['messages'][5:5] = pair('bulk' + str(index), 'read_file', {'path': 'f.py'},
                                          {'ok': True, 'content': 'x' * 3500})
        self.store.save(state, 'summary_input_overflow')
        result = engine.run(tid)
        self.assertEqual(result['phase'], 'completed')
        self.assertEqual(result['semantic_context']['generation'], 2)
        self.assertEqual(result['last_compaction']['status'], 'installed')
        self.assertEqual(len(model.seen), 3)

    def test_unpaired_history_and_user_only_overflow_never_request_summary(self):
        engine, tid, model, state = self.fixture([summary(), final()])
        state['messages'].pop()
        self.store.save(state, 'unpaired')
        result = engine.run(tid)
        self.assertEqual(result['phase'], 'failed')
        self.assertEqual(model.seen, [])
        engine, tid, model, state = self.fixture([summary(), final()])
        state['messages'] = state['messages'][:2]
        state['messages'][1]['content'] = 'u' * 33000
        self.store.save(state, 'user_only_overflow')
        result = engine.run(tid)
        self.assertEqual(result['reason'], 'context_limit')
        self.assertEqual(result['messages'], state['messages'])
        self.assertEqual(model.seen, [])

    def test_queued_batch_and_recovering_partial_patch_finish_before_summary(self):
        engine, tid, model, state = self.fixture([summary(), final()])
        body = '*** Begin Patch\n*** Add File: first\n+one\n*** Add File: second\n+two\n*** End Patch'
        calls = tool('apply_patch_text', {'patch': body}, 'patch')['message']['tool_calls']
        calls += tool('read_file', {'path': 'second'}, 'read')['message']['tool_calls']
        state['messages'].append({'role': 'assistant', 'content': None, 'tool_calls': calls})
        state['phase'], state['tool_queue'], state['consumed_call_ids'] = 'tool_queued', calls, ['patch', 'read']
        self.store.save(state, 'queued_batch')
        def interrupted(event, current):
            if event == 'after_tool_effect':
                raise StorageUnavailable('simulated_crash')
        engine.hook = interrupted
        with self.assertRaises(StorageUnavailable):
            engine.run(tid)
        self.assertEqual(model.seen, [])
        self.assertEqual((self.root / 'first').read_text(), 'one\n')
        self.assertFalse((self.root / 'second').exists())
        complete = model.complete
        def summary_after_tools(*args):
            self.assertEqual((self.root / 'second').read_text(), 'two\n')
            durable = self.store.load(tid)
            self.assertIsNone(durable['pending'])
            self.assertEqual(durable['tool_queue'], [])
            self.assertEqual([m.get('tool_call_id') for m in durable['messages'][-2:]], ['patch', 'read'])
            return complete(*args)
        model.complete = summary_after_tools
        engine.hook = lambda *_: None
        result = engine.run(tid)
        self.assertEqual(result['phase'], 'completed')
        self.assertEqual(result['metrics']['tool_attempts'], 2)
        self.assertEqual(len(model.seen), 2)

    def test_actual_cost_exceeded_by_summary_blocks_regular_decision(self):
        response = {**summary(), 'usage': {'input_tokens': 1000000, 'output_tokens': 20}}
        engine, tid, model, _ = self.fixture([response, final()], input_price=1, output_price=1,
                                             max_cost_usd=0.1)
        result = engine.run(tid)
        self.assertEqual(result['reason'], 'cost_limit')
        self.assertEqual(len(model.seen), 1)
        self.assertEqual(result['metrics']['input_tokens'], 1000000)

    def test_summary_preflight_cost_refusal_allows_cheaper_valid_decision(self):
        model = ScriptedModel([final('done')])
        engine = Engine(self.store, Tools(self.root), model)
        tid = engine.create('Task', Limits(max_context_bytes=100000, auto_compact_bytes=1,
                                          input_price=1, output_price=1))
        state = self.store.load(tid)
        for index in range(30):
            state['messages'] += pair(str(index), 'read_file', {'path': 'f.py'},
                                      {'ok': True, 'content': 'x'})
        _, stats = build_context(state['messages'], engine._schemas(), 100000)
        _, summary_bytes, _ = compaction.prepare(state['messages'], None, 100000)
        # Index wrappers can make the summary request larger than short raw batches.
        decision_reservation = (stats['retained_bytes'] + 4096 + 1024) / 1e6
        summary_reservation = (summary_bytes + 4096 + 1024) / 1e6
        self.assertGreater(summary_reservation, decision_reservation)
        state['limits']['max_cost_usd'] = (decision_reservation + summary_reservation) / 2
        self.store.save(state, 'fixture')

        result = engine.run(tid)
        self.assertEqual(result['phase'], 'completed')
        self.assertEqual(len(model.seen), 1)
        self.assertNotIn('last_compaction', result)
        self.assertNotIn('auto_compaction', result)
        self.assertAlmostEqual(result['metrics']['reserved_usd'], decision_reservation)

    def test_unknown_usage_after_deadline_still_requires_confirmation(self):
        engine, tid, model, _ = self.fixture([{**summary(), 'usage': None}, final()])
        now = time.time()
        with patch('agent_lab.engine.time.time', side_effect=lambda: now if not model.seen else now + 1000):
            result = engine.run(tid)
        self.assertEqual(result['phase'], 'needs_confirmation')
        self.assertEqual(result['reason'], 'usage_unknown')
        self.assertEqual(len(model.seen), 1)

    def test_whole_group_omission_triggers_semantic_summary_below_watermark(self):
        engine,tid,model,_=self.fixture([summary(),summary(),final()],auto_compact_bytes=31999,
                                      max_auto_compactions=2,max_steps=8)
        state=self.store.load(tid)
        for i in range(16):
            state['messages']+=pair('bulk'+str(i),'read_file',{'path':'f.py'},
                                    {'ok':True,'content':'x'*3000})
        before=copy.deepcopy(state['messages'])
        _,stats=build_context(before,engine._schemas(),32000)
        self.assertGreater(stats['dropped_pairs'],0)
        self.assertLess(stats['retained_bytes'],31999)
        self.store.save(state,'would_omit_history')
        result=engine.run(tid)
        self.assertEqual(result['phase'],'completed')
        self.assertIsNotNone(result.get('semantic_context'))
        self.assertEqual(model.seen[0][0]['content'],compaction.SUMMARY_INSTRUCTIONS)
        self.assertEqual(result['messages'][:len(before)],before)
        installed=[e for e in self.store.events(tid) if e['kind']=='compaction_installed']
        self.assertTrue(installed)
        self.assertTrue(all(e.get('compaction_automatic') is True for e in installed))
        self.assertTrue(all(len(e.get('summary_sha256',''))==64 for e in installed))
