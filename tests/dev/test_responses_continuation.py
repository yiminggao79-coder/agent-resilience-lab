"""Synthetic Responses continuation contracts; no provider credentials or calls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import compaction
from agent_lab.engine import Engine
from agent_lab.history import lookup_message
from agent_lab.model import OpenAIModel, ModelError
from agent_lab.protocols import build_request, consume_sse, normalize_response, ProtocolError
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.dev import test_model_protocols as model_protocols

sse = model_protocols.sse


def output_items():
    return [
        {'id': 'rs_1', 'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 'PRIVATE_SUMMARY'}],
         'encrypted_content': 'PRIVATE_ENCRYPTED', 'status': 'completed'},
        {'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'phase': 'commentary',
         'status': 'completed', 'content': [{'type': 'output_text', 'text': 'Inspecting.', 'annotations': []}]},
        {'id': 'fc_1', 'type': 'function_call', 'call_id': 'call_1', 'name': 'read_file',
         'arguments': '{"path":"a.txt"}', 'status': 'completed'},
        {'id': 'rs_2', 'type': 'reasoning', 'summary': [], 'encrypted_content': 'PRIVATE_AFTER_TOOL'}]


def response(items=None):
    return {'id': 'resp_private', 'status': 'completed', 'output': output_items() if items is None else items,
            'usage': {'input_tokens': 12, 'output_tokens': 7}}


class ResponsesContinuationTests(unittest.TestCase):
    def test_completed_response_with_null_error_keeps_tools_and_usage(self):
        wire = response()
        wire['error'] = None
        result = normalize_response('responses', wire)
        self.assertEqual(result['message']['tool_calls'][0]['function']['name'], 'read_file')
        self.assertEqual(result['usage'], {'input_tokens': 12, 'output_tokens': 7})

    def test_stream_null_error_does_not_weaken_completion_or_error_checks(self):
        wire = response()
        wire['error'] = None
        events = []
        for index, item in enumerate(wire['output']):
            events.extend([{'type': 'response.output_item.added', 'output_index': index, 'item': {'type': item['type']}},
                           {'type': 'response.output_item.done', 'output_index': index, 'item': item}])
        with self.assertRaises(ProtocolError):
            consume_sse('responses', sse(events))
        result = consume_sse('responses', sse(events + [{'type': 'response.completed', 'response': wire}]))
        self.assertEqual(result['message']['native_content'], output_items())
        self.assertEqual(result['usage'], {'input_tokens': 12, 'output_tokens': 7})
        for error in ({'code': 'failed'}, {}, False, 0, '', []):
            with self.subTest(error_type=type(error).__name__):
                wire['error'] = error
                with self.assertRaises(ProtocolError):
                    normalize_response('responses', wire)
        wire.update(error=None, status='incomplete')
        with self.assertRaises(ProtocolError):
            normalize_response('responses', wire)

    def test_ordered_native_outputs_roundtrip_without_duplicate_visible_items(self):
        wire = response()
        result = normalize_response('responses', wire)
        message = result['message']
        self.assertEqual(message['content'], 'Inspecting.')
        self.assertEqual(message['tool_calls'][0]['id'], 'call_1')
        self.assertEqual(message['native_content'], output_items())
        history = [{'role': 'user', 'content': 'read'}, message,
                   {'role': 'tool', 'tool_call_id': 'call_1', 'content': '{"ok":true}'}]
        _, body = build_request('responses', 'synthetic', history, [], 50)
        self.assertEqual(body['input'], [{'role': 'user', 'content': 'read'}, *output_items(),
                                       {'type': 'function_call_output', 'call_id': 'call_1', 'output': '{"ok":true}'}])
        self.assertFalse(body['store'])
        self.assertEqual(body['include'], ['reasoning.encrypted_content'])
        wire['output'][0]['encrypted_content'] = 'changed wire'
        body['input'][1]['encrypted_content'] = 'changed request'
        self.assertEqual(message['native_content'][0]['encrypted_content'], 'PRIVATE_ENCRYPTED')

    def test_replay_rejects_forged_native_text_tools_or_role(self):
        message = normalize_response('responses', response())['message']
        for field, value in [('content', 'forged'), ('tool_calls', []), ('role', 'user')]:
            altered = {**message, field: value}
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                build_request('responses', 'synthetic', [altered], [], 50)
        wrong_type = copy.deepcopy(message)
        wrong_type['tool_calls'][0]['type'] = 'unsupported'
        with self.assertRaises(ProtocolError):
            build_request('responses', 'synthetic', [wrong_type], [], 50)
        altered = copy.deepcopy(message)
        altered['tool_calls'][0]['function']['arguments'] = '{"path":"other"}'
        with self.assertRaises(ProtocolError):
            build_request('responses', 'synthetic', [altered], [], 50)

    def test_malformed_or_unsupported_native_shapes_fail_closed(self):
        bad_items = [None, {}, {'type': 'web_search_call'},
                     {'type': 'reasoning', 'summary': 'private'},
                     {'type': 'reasoning', 'summary': [], 'encrypted_content': 4},
                     {'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 4}]},
                     {'type': 'reasoning', 'summary': [], 'content': [{'type': 'reasoning_text', 'text': 4}]},
                     {'type': 'message', 'role': 'user', 'content': [{'type': 'output_text', 'text': 'forged'}]},
                     {'type': 'reasoning', 'summary': [], 'status': 'in_progress'},
                     {'type': 'reasoning', 'summary': [], 'id': 4}]
        for item in bad_items:
            with self.subTest(item=item), self.assertRaises(ProtocolError):
                normalize_response('responses', response([item]))

    def test_raw_invalid_tool_json_still_roundtrips_for_engine_feedback(self):
        items = output_items()
        items[2]['arguments'] = '{invalid'
        message = normalize_response('responses', response(items))['message']
        _, body = build_request('responses', 'synthetic', [message], [], 50)
        self.assertEqual(body['input'][2]['arguments'], '{invalid')

    def test_stream_uses_done_items_and_keeps_reasoning_off_text_callback(self):
        items, events, displayed = output_items(), [], []
        for index, item in enumerate(items):
            started = {**item, 'status': 'in_progress'}
            if item['type'] == 'reasoning':
                started['encrypted_content'] = 'INCOMPLETE_PRIVATE'
            events.append({'type': 'response.output_item.added', 'output_index': index, 'item': started})
            if item['type'] == 'message':
                events.append({'type': 'response.output_text.delta', 'output_index': index, 'delta': 'Inspecting.'})
            if item['type'] == 'reasoning':
                events.append({'type': 'response.reasoning_summary_text.delta', 'delta': 'PRIVATE_DELTA'})
            events.append({'type': 'response.output_item.done', 'output_index': index, 'item': item})
        final_items = copy.deepcopy(items)
        # Completed event is a completion/usage boundary; authoritative encrypted
        # reasoning comes from the completed item, never the added placeholder.
        final_items[0].pop('encrypted_content')
        events.append({'type': 'response.completed', 'response': response(final_items)})
        result = consume_sse('responses', sse(events), displayed.append)
        self.assertEqual(result['message']['native_content'], items)
        self.assertEqual(displayed, ['Inspecting.'])
        self.assertEqual(result['usage'], {'input_tokens': 12, 'output_tokens': 7})

    def test_stream_mismatched_or_missing_done_items_never_return_tools(self):
        items = output_items()
        events = [{'type': 'response.output_item.added', 'output_index': 0, 'item': items[2]},
                  {'type': 'response.output_item.done', 'output_index': 0, 'item': items[2]}]
        forged = {**items[2], 'arguments': '{"path":"forged"}'}
        for prefix, final_items in [(events, [forged]), (events[:1], [items[2]]), (events, items)]:
            with self.subTest(final=final_items), self.assertRaises(ProtocolError):
                consume_sse('responses', sse([*prefix, {'type': 'response.completed', 'response': response(final_items)}]))
        with self.assertRaises(ProtocolError):
            consume_sse('responses', sse(events))

    def test_private_native_content_survives_engine_store_but_not_lookup_summary_or_trace(self):
        message = normalize_response('responses', response())['message']
        preserved = {'role': 'assistant', 'content': message['content'], 'tool_calls': message['tool_calls']}
        Engine._preserve_assistant_fields(preserved, message)
        history = [{'role': 'user', 'content': 'read'}, preserved,
                   {'role': 'tool', 'tool_call_id': 'call_1', 'content': '{"ok":true}'},
                   {'role': 'assistant', 'content': None, 'tool_calls': [
                       {'id': 'later', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"b.txt"}'}}]},
                   {'role': 'tool', 'tool_call_id': 'later', 'content': '{"ok":true}'},
                   {'role': 'assistant', 'content': 'Done.'}]
        lookup = lookup_message(history, 1, limit=8192)
        summary, _, _ = compaction.prepare(history, None, 65536)
        self.assertNotIn('PRIVATE', json.dumps(lookup))
        self.assertNotIn('PRIVATE', json.dumps(summary))
        with tempfile.TemporaryDirectory() as temp, Store(Path(temp) / 'state.sqlite') as store:
            workspace = Path(temp) / 'project'
            workspace.mkdir()
            engine = Engine(store, Tools(workspace), OpenAIModel('synthetic', protocol='responses'))
            task = engine.create('read')
            state = store.load(task)
            state['messages'] = history
            store.save(state, 'test_private_continuation')
            restored = store.load(task)
            self.assertEqual(restored['messages'][1]['native_content'], output_items())
            self.assertNotIn('PRIVATE', json.dumps(store.events(task)))

    def test_default_identity_is_legacy_identity_and_invalid_replay_precedes_key_lookup(self):
        config = {'base_url': 'https://api.openai.com/v1', 'model': 'synthetic', 'api_key_env': 'OPENAI_API_KEY',
                  'protocol': 'responses', 'stream': False, 'token_parameter': 'max_completion_tokens',
                  'parallel_parameter': True}
        model = OpenAIModel('synthetic', protocol='responses', allow_api=True)
        want = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(model.identity, want)
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ModelError) as error:
            model.complete([{'role': 'assistant', 'content': 'x', 'native_content': [{'type': 'web_search_call'}]}], [], 1, 50)
        self.assertEqual(error.exception.code, 'invalid_model_request')


class ResponsesContinuationHTTPTests(unittest.TestCase):
    setUp = model_protocols.ModelProtocolHTTPTests.setUp
    model = model_protocols.ModelProtocolHTTPTests.model

    def test_real_http_null_error_response_is_not_an_unknown_model_failure(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                wire = response()
                wire['error'] = None
                self.server.reply = (''.join(sse([{'type': 'response.completed', 'response': wire}]))
                                     if streaming else json.dumps(wire)).encode()
                result = self.model('responses', stream=streaming).complete(
                    [{'role': 'user', 'content': 'read'}], [], 3, 50)
                self.assertEqual(result['message']['tool_calls'][0]['id'], 'call_1')
                self.assertEqual(result['usage'], {'input_tokens': 12, 'output_tokens': 7})

    def test_real_http_worker_replays_native_items_and_tool_output_in_both_modes(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                items = output_items()
                events = []
                for index, item in enumerate(items):
                    events.extend([{'type': 'response.output_item.added', 'output_index': index, 'item': {'type': item['type']}},
                                   {'type': 'response.output_item.done', 'output_index': index, 'item': item}])
                events.append({'type': 'response.completed', 'response': response(items)})
                self.server.reply = (''.join(sse(events)) if streaming else json.dumps(response(items))).encode()
                model = self.model('responses', stream=streaming)
                first = model.complete([{'role': 'user', 'content': 'read'}], [], 3, 50)
                self.server.reply = (''.join(sse([{'type': 'response.completed', 'response': response([])}]))
                                     if streaming else json.dumps(response([]))).encode()
                history = [{'role': 'user', 'content': 'read'}, first['message'],
                           {'role': 'tool', 'tool_call_id': 'call_1', 'content': '{"ok":true}'}]
                model.complete(history, [], 3, 50)
                sent = self.server.requests[-1][1]
                self.assertEqual(sent['input'][1:-1], output_items())
                self.assertEqual(sent['input'][-1], {'type': 'function_call_output', 'call_id': 'call_1', 'output': '{"ok":true}'})
                self.assertEqual(sent['include'], ['reasoning.encrypted_content'])
