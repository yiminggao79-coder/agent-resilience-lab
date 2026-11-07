"""Packing contracts, not model-summary or Coding quality scores."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agent_lab import compaction
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from tests.dev.test_history import pair
from tests.support import ScriptedModel, final


def archive(count=8, text=None):
    messages = [{'role': 'system', 'content': 'Host safety.'},
                {'role': 'user', 'content': 'Preserve the API and verify saved code.'}]
    for index in range(count):
        batch = pair('c'+str(index), 'read_file', {'path': 'm'+str(index)+'.py'},
                     {'ok': True, 'content': 'def value(): return 1'})
        batch[0].update(content=text or ('observed code and tests '+str(index)+'; ')*110,
                        reasoning_content='PRIVATE_REASONING', native_content=[{'text': 'PRIVATE_NATIVE'}])
        messages.extend(batch)
    return messages


def response(text='Verified edits; preserve API; next run tests.'):
    return {'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}


def wire_size(request):
    return len(json.dumps({'messages': request, 'tools': []}, ensure_ascii=False).encode())


class IncrementalCompactionTests(unittest.TestCase):
    def test_oversized_input_selects_largest_complete_fitting_prefix(self):
        messages = archive();before = copy.deepcopy(messages)
        request, size, end = compaction.prepare(messages, None, 12500)
        self.assertGreater(end, 2)
        self.assertLess(end, len(messages)-2)
        self.assertEqual(size, wire_size(request))
        self.assertLessEqual(size, 12500)
        compaction.validate_history(messages[:end])
        data = json.loads(request[1]['content'])
        self.assertEqual(data['source_range'], [0, end])
        self.assertEqual(data['messages'][-1]['index'], end-1)
        extended = copy.deepcopy(data)
        for index in range(end, end+2):
            public = {k:v for k,v in messages[index].items()
                      if k in ('role','content','tool_calls','tool_call_id')}
            extended['messages'].append({'index': index, 'message': public})
        extended['source_range'][1] = end+2
        next_request = [request[0], {'role': 'user', 'content': json.dumps(extended, ensure_ascii=False)}]
        self.assertGreater(wire_size(next_request), 12500)
        self.assertNotIn('PRIVATE_', json.dumps(request))
        self.assertEqual(messages, before)

    def test_successive_source_cursors_cover_without_gaps_or_replaying_prefix(self):
        messages = archive();before = copy.deepcopy(messages);previous = None;ends = []
        while previous is None or previous['covered_until'] < len(messages)-2:
            request, size, end = compaction.prepare(messages, previous, 12500)
            start = previous['covered_until'] if previous else 0
            data = json.loads(request[1]['content'])
            self.assertEqual(data['source_range'], [start, end])
            self.assertEqual([x['index'] for x in data['messages']], list(range(start, end)))
            self.assertLessEqual(size, 12500)
            previous = compaction.completed_record(messages, end, response(), 'synthetic', previous)
            self.assertEqual(compaction.validate_record(messages, previous, 'synthetic'), end)
            ends.append(end)
            self.assertLessEqual(len(ends), 8)
        self.assertGreater(len(ends), 1)
        self.assertEqual(ends[-1], len(messages)-2)
        self.assertEqual(messages, before)

    def test_previous_notes_consume_budget_and_keep_the_same_source_basis(self):
        messages = archive()
        old = compaction.completed_record(messages, 4, response('Prior decisions. '*250), 'synthetic')
        request, size, end = compaction.prepare(messages, old, 12500)
        data = json.loads(request[1]['content'])
        self.assertEqual(data['previous_summary'], old['summary'])
        self.assertEqual(data['source_range'][0], 4)
        self.assertGreater(end, 4)
        self.assertLess(end, len(messages)-2)
        self.assertLessEqual(size, 12500)
        self.assertTrue(all(x['index'] >= 4 for x in data['messages']))

    def test_utf8_byte_bound_and_indivisible_multi_call_batch(self):
        messages = archive(6, '中文😀 evidence '*180)
        # A single assistant invokes two tools. Neither result can be orphaned.
        first = messages[2]
        first['tool_calls'].append({'id': 'extra', 'type': 'function',
            'function': {'name': 'read_file', 'arguments': '{"path":"other.py"}'}})
        messages.insert(4, {'role': 'tool', 'tool_call_id': 'extra', 'content': '{"ok":true}'})
        request, size, end = compaction.prepare(messages, None, 14000)
        self.assertLessEqual(size, 14000)
        self.assertEqual(size, wire_size(request))
        self.assertNotIn(end, (3, 4))
        compaction.validate_history(messages[:end])
        self.assertLess(end, len(messages)-2)

    def test_full_fit_retains_the_existing_complete_projection(self):
        messages = archive(3, 'Brief observation.')
        request, size, end = compaction.prepare(messages, None, 12500)
        self.assertEqual(end, len(messages)-2)
        data = json.loads(request[1]['content'])
        self.assertEqual(data['source_range'], [0, end])
        self.assertEqual([x['index'] for x in data['messages']], list(range(end)))
        self.assertEqual(data['messages'][2]['message']['tool_calls'], messages[2]['tool_calls'])
        self.assertEqual(size, wire_size(request))
        self.assertNotIn('PRIVATE_', json.dumps(request))

    def test_large_tool_result_is_previewed_with_a_retrievable_original_identity(self):
        messages = archive(3, 'Brief observation.')
        messages[3]['content'] = json.dumps({'ok': True, 'content': '中文'*6000}, ensure_ascii=False)
        raw = messages[3]['content'].encode()
        request, size, end = compaction.prepare(messages, None, 12500)
        data = json.loads(request[1]['content'])
        item = next(row for row in data['messages'] if row['index'] == 3)
        preview = json.loads(item['message']['content'])
        self.assertEqual(preview['lookup_index'], 3)
        self.assertEqual(preview['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(preview['original_bytes'], len(raw))
        self.assertLessEqual(size, 12500)

    def test_oversized_single_batch_is_not_split_or_silently_truncated(self):
        messages = archive(2, 'huge '*10000);before = copy.deepcopy(messages)
        with self.assertRaisesRegex(ValueError, 'compaction_context_limit'):
            compaction.prepare(messages, None, 12500)
        self.assertEqual(messages, before)

    def test_malformed_or_already_consumed_prefix_does_not_create_a_fake_summary(self):
        messages = archive(3, 'Brief observation.')
        old = compaction.completed_record(messages, len(messages)-2, response(), 'synthetic')
        with self.assertRaisesRegex(ValueError, 'compaction_no_new_content'):
            compaction.prepare(messages, old, 12500)
        messages[3]['tool_call_id'] = 'orphan'
        with self.assertRaisesRegex(ValueError, 'compaction_invalid_history'):
            compaction.prepare(messages, None, 12500)

    def test_runtime_advances_semantic_cursor_even_when_old_view_already_dropped_history(self):
        with tempfile.TemporaryDirectory() as temp, Store(Path(temp)/'tasks.sqlite') as store:
            root = Path(temp)/'work';root.mkdir()
            notes = {**final('Observed previous edits, rejected approaches and tests; verify current files.'),
                     'finish_reason': 'stop'}
            model = ScriptedModel([copy.deepcopy(notes), copy.deepcopy(notes), final()])
            engine = Engine(store, CodingTools(root), model)
            task = engine.create('Preserve requirements and finish Coding.', Limits(
                max_context_bytes=32000, auto_compact_bytes=1, max_auto_compactions=2))
            state = store.load(task)
            state['messages'].append({'role': 'assistant', 'content': 'old discussion '*500})
            state['messages'] += pair('old', 'read_file', {'path': 'f.py'}, {'ok': True, 'content': 'old'})
            for index in range(20):
                state['messages'] += pair('bulk'+str(index), 'read_file', {'path': 'f.py'},
                                          {'ok': True, 'content': 'x'*3500})
            state['messages'] += pair('latest', 'read_file', {'path': 'f.py'}, {'ok': True, 'content': 'latest'})
            original = copy.deepcopy(state['messages']);store.save(state, 'incremental_fixture')
            result = engine.run(task)
            self.assertEqual(result['phase'], 'completed')
            self.assertEqual(result['semantic_context']['generation'], 2)
            self.assertEqual(result['last_compaction']['status'], 'installed')
            self.assertEqual(result['messages'][:-1], original)
            installed = [e for e in store.events(task) if e['kind'] == 'compaction_installed']
            self.assertEqual(len(installed), 2)
            self.assertLess(installed[0]['covered_messages'], installed[1]['covered_messages'])
            self.assertEqual(installed[-1]['compaction_generation'], 2)

    def test_automatic_intermediate_summary_advances_until_agent_view_fits(self):
        from agent_lab.context import build_context, ContextLimit
        with tempfile.TemporaryDirectory() as temp, Store(Path(temp)/'tasks.sqlite') as store:
            root=Path(temp)/'work';root.mkdir()
            notes={**final('Preserve all user constraints; verify files and tests.'),'finish_reason':'stop'}
            model=ScriptedModel([copy.deepcopy(notes) for _ in range(5)]+[final()])
            engine=Engine(store,CodingTools(root),model)
            task=engine.create('Continue the project.',Limits(max_context_bytes=24000,
                               auto_compact_bytes=1,max_auto_compactions=5,max_steps=12))
            state=store.load(task)
            for stage in range(3):
                for n in range(3):
                    state['messages']+=pair('s%d-b%d'%(stage,n),'read_file',{'path':'f.py'},
                                           {'ok':True,'content':'x'*3000})
                state['messages'].append({'role':'assistant','content':'observed final '+('y'*6000)})
            state['messages']+=pair('newest','read_file',{'path':'f.py'},{'ok':True,'content':'latest'})
            original=copy.deepcopy(state['messages'])
            with self.assertRaises(ContextLimit):build_context(original,engine._schemas(),24000)
            _,_,end=compaction.prepare(original,None,24000)
            first=compaction.completed_record(original,end,notes,model.identity)
            with self.assertRaises(ContextLimit):build_context(original,engine._schemas(),24000,semantic=first)
            store.save(state,'intermediate_fixture')
            result=engine.run(task)
            self.assertEqual(result['phase'],'completed')
            self.assertGreaterEqual(result['semantic_context']['generation'],2)
            self.assertEqual(result['messages'][:len(original)],original)
            self.assertLessEqual(result['metrics']['model_calls'],6)

    def test_derived_ledger_yields_space_to_semantic_notes_without_losing_archive(self):
        from agent_lab.context import build_context
        messages=[{'role':'system','content':'Host safety.'},{'role':'user','content':'Requirement. '*650}]
        for n in range(36):
            messages+=pair('c'+str(n),'write_file',{'path':'a'*170+str(n)+'.py'},
                           {'ok':True,'sha256':'a'*64})
        before=copy.deepcopy(messages)
        record=compaction.completed_record(messages,len(messages)-2,response('Validated notes. '*50),'synthetic')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);tools=CodingTools(root).schemas()
            view,stats=build_context(messages,tools,24000,semantic=record)
        self.assertLessEqual(stats['retained_bytes'],24000)
        ledger=json.loads(view[0]['content'])
        self.assertGreater(ledger['facts_omitted'],0)
        self.assertEqual(messages,before)
        self.assertIn(messages[1],view)
        self.assertTrue(any(m.get('tool_call_id')=='c35' for m in view))
        self.assertTrue(any(m.get('content','').startswith(compaction.SUMMARY_BOUNDARY) for m in view))

    def test_summary_uses_explicit_turn_output_ceiling_without_hidden_1024_cap(self):
        from agent_lab.tools import Tools
        class Capture(ScriptedModel):
            def complete(self,messages,tools,timeout,max_tokens):
                self.output_ceiling=max_tokens
                return super().complete(messages,tools,timeout,max_tokens)
        with tempfile.TemporaryDirectory() as temp, Store(Path(temp)/'tasks.sqlite') as store:
            root=Path(temp)/'work';root.mkdir();model=Capture([response()])
            engine=Engine(store,Tools(root),model);task=engine.create('Continue.',Limits(max_output_tokens=4096))
            state=store.load(task);state['messages'].append({'role':'assistant','content':'Observed earlier changes.'})
            store.save(state,'output_fixture');engine.compact(task)
            self.assertEqual(model.output_ceiling,4096)

    def test_new_natural_input_releases_completed_prior_turn_batch_for_summary(self):
        messages=archive(3,'Actual observed work.')
        messages.append({'role':'assistant','content':'Previous Turn completed.'})
        messages.append({'role':'user','content':'Now implement a different follow-up.'})
        request,size,end=compaction.prepare(messages,None,24000)
        self.assertEqual(end,len(messages)-1)
        self.assertEqual(json.loads(request[1]['content'])['source_range'][1],end)
        record=compaction.completed_record(messages,end,response(),'synthetic')
        compaction.validate_record(messages,record)

    def test_runtime_notices_do_not_release_current_tool_batch(self):
        messages=archive(3,'Actual observed work.')
        for flag in ('_runtime_control','_file_context','_project_context'):
            current=copy.deepcopy(messages)+[{'role':'user','content':'Runtime metadata',flag:True}]
            _,_,end=compaction.prepare(current,None,24000)
            self.assertEqual(end,len(messages)-2)

    def test_current_complete_batch_results_adapt_to_remaining_context_budget(self):
        from agent_lab.context import build_context
        with tempfile.TemporaryDirectory() as temp, Store(Path(temp)/'tasks.sqlite') as store:
            root=Path(temp)/'work';root.mkdir();engine=Engine(store,CodingTools(root),ScriptedModel([]))
            messages=[{'role':'system','content':'Host rules.'},{'role':'user','content':'Requirements. '*550}]
            calls=[{'id':'r'+str(i),'type':'function','function':{'name':'read_file',
                    'arguments':json.dumps({'path':str(i)+'.py'})}} for i in range(5)]
            messages.append({'role':'assistant','content':None,'tool_calls':calls})
            for i in range(5):
                messages.append({'role':'tool','tool_call_id':'r'+str(i),
                    'content':json.dumps({'ok':False,'error':'synthetic_error','detail':'x'*2200})})
            before=copy.deepcopy(messages)
            view,stats=build_context(messages,engine._schemas(),28000)
            self.assertLessEqual(stats['retained_bytes'],28000)
            self.assertEqual([m['tool_call_id'] for m in view if m['role']=='tool'],['r'+str(i) for i in range(5)])
            compact=[json.loads(m['content']) for m in view if m['role']=='tool' and
                     json.loads(m['content']).get('adaptive_preview')]
            self.assertTrue(compact)
            for preview in compact:
                index=preview['archive_message_index']
                self.assertEqual(preview['sha256'],hashlib.sha256(messages[index]['content'].encode()).hexdigest())
                self.assertEqual(preview['outcome'],{'ok':False,'error':'synthetic_error'})
            self.assertEqual(messages,before)

    def test_summary_input_budget_is_independent_of_larger_agent_working_window(self):
        with tempfile.TemporaryDirectory() as temp, Store(Path(temp)/'tasks.sqlite') as store:
            root=Path(temp)/'work';root.mkdir()
            notes={**final('Source-bound compact notes; continue current task.'),'finish_reason':'stop'}
            model=ScriptedModel([copy.deepcopy(notes),copy.deepcopy(notes),final()])
            engine=Engine(store,CodingTools(root),model)
            limits=Limits(max_context_bytes=64000,max_summary_input_bytes=18000,
                          auto_compact_bytes=20000,max_auto_compactions=2,max_steps=8)
            task=engine.create('Keep requirements; implement follow-up.',limits);state=store.load(task)
            state['messages']+=archive(8,'Observed code and failed verification. '*100)[2:]
            original=copy.deepcopy(state['messages']);store.save(state,'dual_window_fixture')
            result=engine.run(task)
            self.assertEqual(result['phase'],'completed')
            summaries=[m for m in model.seen if m[0]['content']==compaction.SUMMARY_INSTRUCTIONS]
            self.assertEqual(len(summaries),2)
            self.assertTrue(all(wire_size(m)<=18000 for m in summaries))
            self.assertEqual(result['semantic_context']['generation'],2)
            self.assertEqual(result['messages'][:len(original)],original)
            for value in (0,-1,True,64001):
                with self.assertRaises(ValueError):Limits(max_context_bytes=64000,max_summary_input_bytes=value)
