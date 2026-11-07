"""Deterministic evidence selection and bounded private-archive projections."""
import copy
import importlib.util
import json
import unittest

from agent_lab.context import build_context, ContextLimit
from agent_lab.tools import ToolError


def pair(call_id, name, args, result):
    return [{'role': 'assistant', 'content': None,
             'tool_calls': [{'id': call_id, 'type': 'function',
                             'function': {'name': name, 'arguments': json.dumps(args)}}]},
            {'role': 'tool', 'tool_call_id': call_id, 'content': json.dumps(result)}]


def ledger(view):
    for message in view:
        if message.get('role') == 'system':
            try:
                value = json.loads(message.get('content', ''))
            except (ValueError, TypeError):
                continue
            if value.get('context_archive'):
                return value
    raise AssertionError('missing deterministic archive evidence ledger')


class HistoryLookupTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('agent_lab.history'),
                             'archive lookup is not implemented')
        from agent_lab.history import lookup_message
        self.lookup = lookup_message

    def test_paginated_ascii_projection_reconstructs_exact_content_after_restart(self):
        original = '中文😀 "quoted" \\ slash\n' * 300
        messages = [{'role': 'tool', 'tool_call_id': 'call', 'content': original,
                     'reasoning_content': 'PRIVATE_REASONING',
                     'native_content': [{'text': 'PRIVATE_NATIVE'}]}]
        reopened = json.loads(json.dumps(messages))
        parts, offset = [], 0
        while True:
            page = self.lookup(reopened, 0, offset, 17)
            self.assertTrue(page['fragment'].isascii())
            self.assertLessEqual(len(page['fragment'].encode('ascii')), 17)
            self.assertEqual(page['offset'], offset)
            parts.append(page['fragment'])
            if page['complete']:
                self.assertIsNone(page['next_offset'])
                break
            offset = page['next_offset']
        projected = json.loads(''.join(parts))
        self.assertEqual(projected, {'role': 'tool', 'tool_call_id': 'call', 'content': original})
        self.assertNotIn('PRIVATE_', ''.join(parts))
        self.assertEqual(self.lookup(messages, 0), self.lookup(reopened, 0))

    def test_lookup_preserves_only_canonical_call_fields_and_caps_fragments(self):
        messages = pair('one', 'apply_patch', {'path': 'calc.py', 'new': 'x' * 12000}, {'ok': True})
        messages[0]['tool_calls'][0]['function']['reasoning_content'] = 'PRIVATE_NESTED'
        page = self.lookup(messages, 0, 0, 8192)
        self.assertEqual(len(page['fragment']), 8192)
        self.assertFalse(page['complete'])
        self.assertNotIn('PRIVATE_NESTED', page['fragment'])
        self.assertEqual(len(page['projection_sha256']), 64)
        for index, offset, limit in [(True, 0, 1), (-1, 0, 1), (2, 0, 1), (0, True, 1), (0, -1, 1), (0, 10**9, 1), (0, 0, True), (0, 0, 0), (0, 0, 8193)]:
            with self.subTest(index=index, offset=offset, limit=limit), self.assertRaises(ToolError):
                self.lookup(messages, index, offset, limit)


class ArchiveEvidenceTests(unittest.TestCase):
    def history(self):
        messages = [{'role': 'system', 'content': 'Rules'}, {'role': 'user', 'content': 'Preserve the add API and verify before claiming success.'}]
        messages += pair('edit', 'apply_patch', {'path': 'calc.py', 'old': 'return a-b', 'new': 'SOURCE_MUST_NOT_ENTER_LEDGER'}, {'ok': True, 'sha256': 'a' * 64})
        messages += pair('failed', 'run_tests', {}, {'ok': False, 'exit_code': 1, 'stderr': 'PRIVATE_TEST_BODY'})
        for i in range(14):
            messages += pair(str(i), 'read_file', {'path': 'data'}, {'ok': True, 'content': 'x' * 2000})
        return messages

    def test_omitted_edit_and_failed_verification_survive_as_indexed_facts(self):
        messages = self.history()
        original = copy.deepcopy(messages)
        view, stats = build_context(messages, [], 7000)
        evidence = ledger(view)
        facts = {f['tool']: f for f in evidence['facts'] if f['tool'] in ('apply_patch', 'run_tests')}
        self.assertEqual(facts['apply_patch']['message_indices'], [2, 3])
        self.assertEqual(facts['apply_patch']['assistant_index'], 2)
        self.assertEqual(facts['apply_patch']['result_indices'], [3])
        self.assertEqual(facts['apply_patch']['path'], 'calc.py')
        self.assertEqual(facts['apply_patch']['outcome'], {'ok': True, 'sha256': 'a' * 64})
        self.assertEqual(facts['run_tests']['message_indices'], [4, 5])
        self.assertEqual(facts['run_tests']['outcome'], {'ok': False, 'exit_code': 1})
        self.assertNotIn('SOURCE_MUST_NOT_ENTER_LEDGER', json.dumps(evidence))
        self.assertNotIn('PRIVATE_TEST_BODY', json.dumps(evidence))
        self.assertGreater(stats['dropped_pairs'], 0)
        self.assertLessEqual(stats['retained_bytes'], 7000)
        self.assertEqual(messages, original)
        self.assertIn('history_lookup', json.dumps(evidence))
        self.assertTrue(any(a <= 3 <= b for a, b in evidence['omitted_ranges']))

    def test_previews_reference_original_indices_and_exclude_private_fields(self):
        messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'inspect'}]
        messages += pair('read', 'read_file', {'path': 'calc.py'}, {'ok': True, 'content': 'z' * 30000})
        messages[2]['reasoning_content'] = 'REASONING_MUST_NOT_ENTER_LEDGER'
        messages[2]['native_content'] = [{'text': 'NATIVE_MUST_NOT_ENTER_LEDGER'}]
        view, _ = build_context(messages, [], 6500)
        preview = json.loads(view[-1]['content'])
        self.assertIn('archive_message_index', preview)
        self.assertEqual(preview['archive_message_index'], 3)
        self.assertEqual(preview['archive_lookup_tool'], 'history_lookup')
        evidence = ledger(view)
        self.assertNotIn('MUST_NOT_ENTER_LEDGER', json.dumps(evidence))

    def test_user_turn_boundaries_and_pending_multi_call_group_are_preserved(self):
        messages = self.history()
        user_index = len(messages)
        messages.append({'role': 'user', 'content': 'New turn: keep all previous constraints and inspect only.'})
        pending = {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'pending1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}},
            {'id': 'pending2', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}]}
        messages += [pending, {'role': 'tool', 'tool_call_id': 'pending1', 'content': '{"ok":true}'}]
        view, _ = build_context(messages, [], 7500)
        self.assertEqual(view[-2:], messages[-2:])
        self.assertEqual([m['content'] for m in view if m['role'] == 'user'], [messages[1]['content'], messages[user_index]['content']])
        self.assertTrue(all(f['user_message_index'] == 1 for f in ledger(view)['facts']))

    def test_ledger_is_bounded_prioritizes_recent_changes_and_reports_omissions(self):
        messages = [{'role': 'user', 'content': 'Preserve this requirement.'}]
        for i in range(80):
            messages += pair(str(i), 'write_file', {'path': 'file' + str(i)}, {'ok': True, 'sha256': format(i, '064x')})
        messages += pair('last', 'read_file', {'path': 'tail'}, {'ok': True, 'content': 'x' * 8000})
        view, stats = build_context(messages, [], 10000)
        evidence = ledger(view)
        self.assertLessEqual(len(json.dumps(evidence, ensure_ascii=False).encode()), 8192)
        self.assertLessEqual(len(evidence['facts']), 32)
        self.assertGreater(evidence['facts_omitted'], 0)
        self.assertIn('file79', json.dumps(view))
        newest_omitted = max(index for a, b in evidence['omitted_ranges'] for index in range(a, b + 1)
                             if messages[index]['role'] == 'tool')
        self.assertEqual(evidence['facts'][0]['message_indices'][-1], newest_omitted)
        self.assertLessEqual(stats['retained_bytes'], 10000)
        with self.assertRaises(ContextLimit):
            build_context([{'role': 'user', 'content': 'requirement' * 1000}], [], 100)

    def test_completed_batch_is_indivisible_and_facts_reference_all_results(self):
        messages = [{'role': 'user', 'content': 'Preserve batch source references.'}]
        first = pair('a', 'apply_patch', {'path': 'calc.py'}, {'ok': True, 'sha256': 'a' * 64, 'content': 'x' * 2400})
        second = pair('b', 'run_tests', {}, {'ok': False, 'exit_code': 1, 'stderr': 'x' * 2400})
        first[0]['tool_calls'] += second[0]['tool_calls']
        messages += [first[0], first[1], second[1]]
        for i in range(4):
            messages += pair('r' + str(i), 'read_file', {'path': 'data'}, {'ok': True, 'content': 'x' * 2000})
        view, _ = build_context(messages, [], 6500)
        evidence = ledger(view)
        self.assertFalse(any(m.get('tool_call_id') in ('a', 'b') for m in view))
        facts = [f for f in evidence['facts'] if f['tool'] in ('apply_patch', 'run_tests')]
        self.assertEqual(len(facts), 2)
        for fact in facts:
            self.assertEqual(fact['assistant_index'], 1)
            self.assertEqual(fact['result_indices'], [2, 3])

    def test_reordered_result_batch_is_not_treated_as_completed(self):
        messages = [{'role': 'user', 'content': 'Keep uncompleted group.'}]
        first = pair('a', 'read_file', {}, {'ok': True, 'content': 'x' * 3000})
        second = pair('b', 'read_file', {}, {'ok': True, 'content': 'y' * 3000})
        first[0]['tool_calls'] += second[0]['tool_calls']
        messages += [first[0], second[1], first[1]]
        messages += pair('last', 'read_file', {}, {'ok': True})
        with self.assertRaises(ContextLimit):
            build_context(messages, [], 5000)


if __name__ == '__main__':
    unittest.main()


class LargeEditLedgerTests(unittest.TestCase):
    def test_confirmed_large_edit_receipt_survives_omission_of_recent_reads(self):
        from agent_lab.context import _tool_facts, _ledger
        messages=pair('changed','edit_file',{'path':'large.py'}, {'ok':True,'sha256':'a'*64})
        for i in range(40):messages+=pair('read'+str(i),'read_file',{'path':'noise'+str(i)}, {'ok':True,'content':'noise'})
        facts=_tool_facts(messages)
        evidence=json.loads(_ledger(facts,list(facts),set(range(len(messages))),32768)['content'])
        self.assertGreater(evidence['facts_omitted'],0)
        self.assertTrue(any(f['tool']=='edit_file' and f['outcome'].get('sha256')=='a'*64 for f in evidence['facts']))
        self.assertLessEqual(len(json.dumps(evidence,ensure_ascii=False).encode()),8192)
