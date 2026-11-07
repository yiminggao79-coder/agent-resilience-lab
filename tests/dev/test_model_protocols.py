import json
import unittest
from agent_lab.protocols import build_request, normalize_response, consume_sse, ProtocolError


SCHEMA = [{'type': 'function', 'function': {'name': 'read_file', 'description': 'read',
           'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}}]


def sse(items, done=False):
    lines = []
    for item in items:
        lines += ['data: ' + json.dumps(item) + '\n', '\n']
    if done:
        lines += ['data: [DONE]\n', '\n']
    return lines


class ProtocolTests(unittest.TestCase):
    def test_chat_token_capability_is_explicit(self):
        path, body = build_request('chat', 'any-model', [{'role': 'user', 'content': 'x'}], SCHEMA,
                                   99, token_parameter='max_tokens', parallel_parameter=False)
        self.assertEqual(path, '/chat/completions')
        self.assertEqual(body['max_tokens'], 99)
        self.assertNotIn('max_completion_tokens', body)
        self.assertNotIn('parallel_tool_calls', body)

    def test_anthropic_tools_and_feedback_are_translated(self):
        history = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'read'},
                   {'role': 'assistant', 'content': None, 'tool_calls': [
                       {'id': 'c', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"a"}'}}]},
                   {'role': 'tool', 'tool_call_id': 'c', 'content': '{"ok":false,"error":"not_found"}'}]
        path, body = build_request('anthropic', 'm', history, SCHEMA, 99)
        self.assertEqual(path, '/messages')
        self.assertEqual(body['system'], 'rules')
        self.assertEqual(body['tools'][0]['input_schema']['required'], ['path'])
        self.assertEqual(body['messages'][-1]['content'][0]['tool_use_id'], 'c')
        self.assertTrue(body['messages'][-1]['content'][0]['is_error'])
        result = normalize_response('anthropic', {'id': 'msg', 'content': [
            {'type': 'tool_use', 'id': 'next', 'name': 'read_file', 'input': {'path': 'b'}}],
            'usage': {'input_tokens': 7, 'output_tokens': 3}, 'stop_reason': 'tool_use'})
        self.assertEqual(json.loads(result['message']['tool_calls'][0]['function']['arguments']), {'path': 'b'})
        self.assertEqual(result['usage'], {'input_tokens': 7, 'output_tokens': 3})

    def test_responses_function_items_and_usage(self):
        history = [{'role': 'user', 'content': 'read'}, {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'c', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"a"}'}}]},
            {'role': 'tool', 'tool_call_id': 'c', 'content': 'result'}]
        path, body = build_request('responses', 'm', history, SCHEMA, 123)
        self.assertEqual(path, '/responses')
        self.assertEqual(body['max_output_tokens'], 123)
        self.assertEqual(body['input'][-1], {'type': 'function_call_output', 'call_id': 'c', 'output': 'result'})
        self.assertEqual(body['tools'][0]['name'], 'read_file')
        r = normalize_response('responses', {'id': 'r', 'status': 'completed', 'output': [
            {'type': 'function_call', 'call_id': 'n', 'name': 'read_file', 'arguments': '{"path":"b"}'}],
            'usage': {'input_tokens': 11, 'output_tokens': 5}})
        self.assertEqual(r['message']['tool_calls'][0]['id'], 'n')

    def test_chat_stream_reassembles_arguments_only_after_done(self):
        chunks = [
            {'id': 'r', 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': 'c', 'type': 'function', 'function': {'name': 'read_', 'arguments': '{"path":'}}]}, 'finish_reason': None}]},
            {'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'function': {'name': 'file', 'arguments': '"a"}'}}]}, 'finish_reason': None}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]},
            {'choices': [], 'usage': {'prompt_tokens': 13, 'completion_tokens': 6}}]
        r = consume_sse('chat', sse(chunks, done=True))
        self.assertEqual(r['message']['tool_calls'][0]['function'], {'name': 'read_file', 'arguments': '{"path":"a"}'})
        self.assertEqual(r['usage'], {'input_tokens': 13, 'output_tokens': 6})
        for lines in (sse(chunks), sse(chunks[:1], done=True)):
            with self.assertRaises(ProtocolError):
                consume_sse('chat', lines)

    def test_anthropic_stream_needs_message_stop_and_complete_tool_input(self):
        chunks = [{'type': 'message_start', 'message': {'id': 'm', 'usage': {'input_tokens': 7, 'output_tokens': 0}}},
                  {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'tool_use', 'id': 'c', 'name': 'read_file', 'input': {}}},
                  {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': '{"path":"a"}'}},
                  {'type': 'content_block_stop', 'index': 0},
                  {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 4}},
                  {'type': 'message_stop'}]
        r = consume_sse('anthropic', sse(chunks))
        self.assertEqual(r['usage']['output_tokens'], 4)
        self.assertEqual(r['message']['tool_calls'][0]['id'], 'c')
        with self.assertRaises(ProtocolError):
            consume_sse('anthropic', sse(chunks[:-1]))

    def test_responses_stream_requires_completed_response(self):
        body = {'id': 'r', 'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'ok'}]}]}
        self.assertEqual(consume_sse('responses', sse([{'type': 'response.completed', 'response': body}]))['message']['content'], 'ok')
        with self.assertRaises(ProtocolError):
            consume_sse('responses', sse([{'type': 'response.output_text.delta', 'delta': 'partial'}]))

    def test_truncation_refusal_and_bad_usage_never_authorize_partial_tool(self):
        for protocol, body in [('chat', {'choices': [{'message': {'role': 'assistant', 'content': 'cut'}, 'finish_reason': 'length'}]}),
                               ('anthropic', {'content': [], 'stop_reason': 'max_tokens'}),
                               ('responses', {'output': [], 'status': 'incomplete'})]:
            with self.subTest(protocol=protocol), self.assertRaises(ProtocolError):
                normalize_response(protocol, body)


class ProtocolBoundaryTests(unittest.TestCase):
    def test_refusal_is_rejected_and_complete_bad_arguments_reach_engine(self):
        bad = {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'c', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{'}}]}, 'finish_reason': 'tool_calls'}]}
        self.assertEqual(normalize_response('chat', bad)['message']['tool_calls'][0]['function']['arguments'], '{')
        bodies = [('responses', {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'no'}]}]}),
            ('anthropic', {'content': [], 'stop_reason': 'refusal'})]
        for protocol, body in bodies:
            with self.subTest(protocol=protocol), self.assertRaises(ProtocolError):
                normalize_response(protocol, body)

    def test_chat_private_reasoning_and_request_id_are_preserved_canonically(self):
        r = normalize_response('chat', {'id': 'private-id', 'choices': [{'message': {
            'role': 'assistant', 'content': 'ok', 'reasoning_content': 'private reasoning'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': True, 'completion_tokens': 4}})
        self.assertEqual(r['message']['reasoning_content'], 'private reasoning')
        self.assertEqual(r['request_id'], 'private-id')
        self.assertIsNone(r['usage'])

    def test_stream_rejects_unclosed_anthropic_block_and_chat_length(self):
        for protocol, events, done in [('anthropic', [
            {'type': 'message_start', 'message': {'id': 'm', 'usage': {}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'tool_use', 'id': 'c', 'name': 'read_file', 'input': {}}},
            {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}}, {'type': 'message_stop'}], False),
            ('chat', [{'choices': [{'index': 0, 'delta': {'content': 'cut'}, 'finish_reason': 'length'}]}], True)]:
            with self.subTest(protocol=protocol), self.assertRaises(ProtocolError):
                consume_sse(protocol, sse(events, done=done))


from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import threading
from unittest.mock import patch
from agent_lab.model import OpenAIModel, ModelError


class ProtocolHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append((self.path, body, {key.lower(): value for key, value in self.headers.items()}))
        self.send_response(200)
        self.end_headers()
        try:
            self.wfile.write(self.server.reply)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ModelProtocolHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ProtocolHTTPHandler)
        self.server.requests = []
        self.server.reply = b'{}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        env = patch.dict(os.environ, {'LAB_PROTOCOL_KEY': 'fake-local-key', 'HTTP_PROXY': 'http://127.0.0.1:1'})
        env.start()
        self.addCleanup(env.stop)

    def model(self, protocol='chat', **kwargs):
        return OpenAIModel('contract', 'http://127.0.0.1:%s/v1' % self.server.server_port,
                           'LAB_PROTOCOL_KEY', True, protocol=protocol, **kwargs)

    def test_all_protocols_use_real_worker_and_protocol_headers(self):
        fixtures = [('chat', '/v1/chat/completions', {'id': 'request-private', 'choices': [{'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}]}),
                    ('responses', '/v1/responses', {'id': 'request-private', 'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'ok'}]}]}),
                    ('anthropic', '/v1/messages', {'id': 'request-private', 'content': [{'type': 'text', 'text': 'ok'}], 'stop_reason': 'end_turn'})]
        for protocol, path, body in fixtures:
            self.server.reply = json.dumps(body).encode()
            with self.subTest(protocol=protocol):
                result = self.model(protocol).complete([{'role': 'user', 'content': 'hi'}], SCHEMA, 3, 50)
                request_path, request_body, headers = self.server.requests[-1]
                self.assertEqual(request_path, path)
                self.assertEqual(result['message']['content'], 'ok')
                self.assertNotIn('request_id', result)
                self.assertEqual(len(result['request_id_hash']), 64)
                if protocol == 'anthropic':
                    self.assertEqual(headers['x-api-key'], 'fake-local-key')
                    self.assertEqual(headers['anthropic-version'], '2023-06-01')
                    self.assertNotIn('authorization', headers)
                else:
                    self.assertEqual(headers['authorization'], 'Bearer fake-local-key')

    def test_worker_stream_assembles_tool_and_rejects_incomplete_response(self):
        events = [{'id': 'r', 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': 'c', 'function': {'name': 'read_file', 'arguments': '{"path":'}}]}, 'finish_reason': None}]},
                  {'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"a"}'}}]}, 'finish_reason': 'tool_calls'}]}]
        self.server.reply = ''.join(sse(events, done=True)).encode()
        model = self.model(stream=True)
        result = model.complete([], SCHEMA, 3, 50)
        self.assertEqual(json.loads(result['message']['tool_calls'][0]['function']['arguments']), {'path': 'a'})
        self.assertTrue(self.server.requests[-1][1]['stream'])
        self.server.reply = ''.join(sse(events)).encode()
        with self.assertRaises(ModelError) as error:
            model.complete([], SCHEMA, 3, 50)
        self.assertEqual(error.exception.code, 'invalid_model_response')
        self.assertTrue(error.exception.uncertain)

    def test_response_size_limit_applies_to_stream(self):
        self.server.reply = b':' + b'x' * (2 * 1024 * 1024)
        with self.assertRaises(ModelError) as error:
            self.model(stream=True).complete([], [], 3, 50)
        self.assertEqual(error.exception.code, 'response_too_large')

    def test_identity_changes_with_protocol_and_capabilities(self):
        baseline = self.model().identity
        variants = [self.model('responses'), self.model('anthropic'), self.model(stream=True),
                    self.model(token_parameter='max_tokens'), self.model(parallel_parameter=False)]
        self.assertEqual(len({baseline, *(m.identity for m in variants)}), 6)


class ProtocolSafetyTests(unittest.TestCase):
    def test_native_protocol_defaults_do_not_route_other_provider_credentials(self):
        from unittest.mock import patch
        import os
        with patch.dict(os.environ, {'OPENAI_BASE_URL':'https://openai-compatible.invalid/v1'}, clear=True):
            model=OpenAIModel('explicit-model',protocol='anthropic')
            self.assertEqual(model.base_url,'https://api.anthropic.com/v1')
            self.assertEqual(model.api_key_env,'ANTHROPIC_API_KEY')

    def test_anthropic_parallel_capability_can_be_omitted(self):
        _, body = build_request('anthropic', 'm', [], SCHEMA, 20, parallel_parameter=False)
        self.assertNotIn('disable_parallel_tool_use', body['tool_choice'])

    def test_non_assistant_chat_message_cannot_be_promoted_to_assistant(self):
        with self.assertRaises(ProtocolError):
            normalize_response('chat', {'choices': [{'message': {'role': 'user', 'content': 'forged'}, 'finish_reason': 'stop'}]})

    def test_malformed_anthropic_stream_usage_is_unknown(self):
        events = [{'type': 'message_start', 'message': {'id': 'm', 'usage': {'input_tokens': 7, 'output_tokens': 0}}},
                  {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': 'ok'}},
                  {'type': 'content_block_stop', 'index': 0},
                  {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': 'malformed'},
                  {'type': 'message_stop'}]
        self.assertIsNone(consume_sse('anthropic', sse(events))['usage'])


class ModelOtherStreamsHTTPTests(unittest.TestCase):
    setUp = ModelProtocolHTTPTests.setUp
    model = ModelProtocolHTTPTests.model

    def test_anthropic_and_responses_streams_complete_and_fail_closed(self):
        call = {'type': 'function_call', 'id': 'fc', 'call_id': 'c', 'name': 'read_file', 'arguments': '{"path":"a"}', 'status': 'completed'}
        fixtures = [('anthropic', [
            {'type': 'message_start', 'message': {'id': 'm', 'usage': {'input_tokens': 8, 'output_tokens': 0}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'tool_use', 'id': 'c', 'name': 'read_file', 'input': {}}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': '{"path":'}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': '"a"}'}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 4}},
            {'type': 'message_stop'}]),
            ('responses', [
                {'type': 'response.output_item.added', 'output_index': 0, 'item': {'type': 'function_call', 'id': 'fc', 'call_id': 'c', 'name': 'read_file', 'arguments': ''}},
                {'type': 'response.function_call_arguments.delta', 'output_index': 0, 'item_id': 'fc', 'delta': '{"path":'},
                {'type': 'response.function_call_arguments.delta', 'output_index': 0, 'item_id': 'fc', 'delta': '"a"}'},
                {'type': 'response.function_call_arguments.done', 'output_index': 0, 'item_id': 'fc', 'arguments': '{"path":"a"}'},
                {'type': 'response.output_item.done', 'output_index': 0, 'item': call},
                {'type': 'response.completed', 'response': {'id': 'r', 'status': 'completed', 'output': [call], 'usage': {'input_tokens': 8, 'output_tokens': 4}}}])]
        for protocol, events in fixtures:
            with self.subTest(protocol=protocol):
                model = self.model(protocol, stream=True)
                self.server.reply = ''.join(sse(events)).encode()
                result = model.complete([], SCHEMA, 3, 50)
                self.assertEqual(result['message']['tool_calls'][0]['id'], 'c')
                self.assertEqual(result['usage'], {'input_tokens': 8, 'output_tokens': 4})
                self.server.reply = ''.join(sse(events[:-1])).encode()
                with self.assertRaises(ModelError) as error:
                    model.complete([], SCHEMA, 3, 50)
                self.assertEqual(error.exception.code, 'invalid_model_response')


class ThinkingProtocolTests(unittest.TestCase):
    def test_thinking_and_effort_are_explicit_wire_options(self):
        for protocol in ('chat', 'anthropic'):
            for mode in ('enabled', 'disabled'):
                with self.subTest(protocol=protocol, mode=mode):
                    _, body = build_request(protocol, 'm', [], [], 40, thinking=mode,
                                            reasoning_effort='max')
                    self.assertEqual(body['thinking'], {'type': mode})
                    if protocol == 'chat':
                        self.assertEqual(body['reasoning_effort'], 'max')
                        self.assertNotIn('output_config', body)
                    else:
                        self.assertEqual(body['output_config'], {'effort': 'max'})
                        self.assertNotIn('reasoning_effort', body)
        _, body = build_request('anthropic', 'm', [], [], 40, thinking='adaptive')
        self.assertEqual(body['thinking'], {'type': 'adaptive'})
        for protocol in ('chat', 'anthropic', 'responses'):
            _, body = build_request(protocol, 'm', [], [], 40)
            self.assertNotIn('thinking', body)
            self.assertNotIn('reasoning_effort', body)
            self.assertNotIn('output_config', body)

    def test_invalid_or_unsupported_thinking_options_are_rejected(self):
        cases = [('chat', {'thinking': True}, 'invalid_thinking'),
                 ('chat', {'thinking': {'type': 'enabled'}}, 'invalid_thinking'),
                 ('chat', {'thinking': 'adaptive'}, 'invalid_thinking'),
                 ('anthropic', {'thinking': 'auto'}, 'invalid_thinking'),
                 ('chat', {'reasoning_effort': 'unlimited'}, 'invalid_reasoning_effort'),
                 ('anthropic', {'reasoning_effort': 1}, 'invalid_reasoning_effort'),
                 ('responses', {'thinking': 'enabled'}, 'unsupported_thinking_options'),
                 ('responses', {'reasoning_effort': 'high'}, 'unsupported_thinking_options')]
        for protocol, options, code in cases:
            with self.subTest(protocol=protocol, options=options):
                with self.assertRaisesRegex(ProtocolError, '^' + code + '$'):
                    build_request(protocol, 'm', [], [], 40, **options)
                with self.assertRaisesRegex(ValueError, '^' + code + '$'):
                    OpenAIModel('m', protocol=protocol, **options)
        for effort in ('minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
            _, body = build_request('chat', 'm', [], [], 40, reasoning_effort=effort)
            self.assertEqual(body['reasoning_effort'], effort)

    def test_anthropic_native_blocks_round_trip_without_duplicate_tools(self):
        blocks = [{'type': 'thinking', 'thinking': 'private', 'signature': 'signed'},
                  {'type': 'text', 'text': 'Before'},
                  {'type': 'tool_use', 'id': 'c', 'name': 'read_file', 'input': {'path': 'a'}},
                  {'type': 'text', 'text': 'After'},
                  {'type': 'redacted_thinking', 'data': 'opaque'}]
        message = normalize_response('anthropic', {'content': blocks, 'stop_reason': 'tool_use'})['message']
        self.assertEqual(message['native_content'], blocks)
        self.assertEqual(message['content'], 'BeforeAfter')
        history = [message, {'role': 'tool', 'tool_call_id': 'c', 'content': 'result'}]
        _, body = build_request('anthropic', 'm', history, SCHEMA, 40)
        self.assertEqual(body['messages'][0]['content'], blocks)
        body['messages'][0]['content'][0]['thinking'] = 'changed'
        self.assertEqual(message['native_content'][0]['thinking'], 'private')
        blocks[0]['thinking'] = 'changed at source'
        self.assertEqual(message['native_content'][0]['thinking'], 'private')

    def test_native_content_cannot_disagree_with_canonical_text_or_tools(self):
        message = {'role': 'assistant', 'content': 'ok', 'native_content': [{'type': 'text', 'text': 'different'}]}
        with self.assertRaisesRegex(ProtocolError, '^native_content_mismatch$'):
            build_request('anthropic', 'm', [message], [], 40)
        message = {'role': 'assistant', 'content': None, 'native_content': [
            {'type': 'tool_use', 'id': 'c', 'name': 'read_file', 'input': {'path': 'a'}}]}
        with self.assertRaisesRegex(ProtocolError, '^native_content_mismatch$'):
            build_request('anthropic', 'm', [message], [], 40)
        message = {'role': 'user', 'content': 'ok', 'native_content': [{'type': 'text', 'text': 'ok'}]}
        with self.assertRaisesRegex(ProtocolError, '^invalid_native_content_role$'):
            build_request('anthropic', 'm', [message], [], 40)

    def test_anthropic_usage_counts_cached_input_before_budget_settlement(self):
        body = {'content': [{'type': 'text', 'text': 'ok'}], 'stop_reason': 'end_turn',
                'usage': {'input_tokens': 10, 'cache_read_input_tokens': 1000,
                          'cache_creation_input_tokens': 20, 'output_tokens': 3}}
        self.assertEqual(normalize_response('anthropic', body)['usage'],
                         {'input_tokens': 1030, 'output_tokens': 3})
        for field in ('cache_read_input_tokens', 'cache_creation_input_tokens'):
            for invalid in (True, -1, '100'):
                with self.subTest(field=field, invalid=invalid):
                    broken = dict(body, usage=dict(body['usage'], **{field: invalid}))
                    self.assertIsNone(normalize_response('anthropic', broken)['usage'])

    def test_anthropic_stream_uses_final_cache_counts_without_adding_initial_usage(self):
        events = [
            {'type': 'message_start', 'message': {'id': 'm', 'usage': {'input_tokens': 5,
                'cache_read_input_tokens': 500, 'cache_creation_input_tokens': 10, 'output_tokens': 0}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': 'ok'}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {
                'input_tokens': 10, 'cache_read_input_tokens': 1000,
                'cache_creation_input_tokens': 20, 'output_tokens': 3}},
            {'type': 'message_stop'}]
        self.assertEqual(consume_sse('anthropic', sse(events))['usage'],
                         {'input_tokens': 1030, 'output_tokens': 3})

    def test_native_tool_arguments_cannot_change_json_types(self):
        message = {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'c', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"flag":1}'}}],
            'native_content': [{'type': 'tool_use', 'id': 'c', 'name': 'read_file', 'input': {'flag': True}}]}
        with self.assertRaisesRegex(ProtocolError, '^native_content_mismatch$'):
            build_request('anthropic', 'm', [message], [], 40)

    def test_invalid_anthropic_thinking_blocks_fail_closed(self):
        for blocks in ({'type': 'thinking'}, [{'type': 'thinking', 'thinking': 1, 'signature': 's'}],
                       [{'type': 'thinking', 'thinking': 'x', 'signature': 1}],
                       [{'type': 'redacted_thinking', 'data': None}]):
            with self.subTest(blocks=blocks), self.assertRaises(ProtocolError):
                normalize_response('anthropic', {'content': blocks, 'stop_reason': 'end_turn'})

    def test_anthropic_stream_preserves_thinking_signature_and_order(self):
        events = [
            {'type': 'message_start', 'message': {'id': 'm', 'usage': {'input_tokens': 3, 'output_tokens': 0}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'private '}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'thought'}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'signature_delta', 'signature': 'sig'}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'signature_delta', 'signature': 'nature'}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'tool_use', 'id': 'c', 'name': 'read_file', 'input': {}}},
            {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': '{"path":"a"}'}},
            {'type': 'content_block_stop', 'index': 1},
            {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 4}},
            {'type': 'message_stop'}]
        message = consume_sse('anthropic', sse(events))['message']
        self.assertEqual(message['native_content'], [
            {'type': 'thinking', 'thinking': 'private thought', 'signature': 'signature'},
            {'type': 'tool_use', 'id': 'c', 'name': 'read_file', 'input': {'path': 'a'}}])
        with self.assertRaises(ProtocolError):
            consume_sse('anthropic', sse(events[:-1]))
        for delta in ({'type': 'thinking_delta', 'thinking': 'bad'},
                      {'type': 'signature_delta', 'signature': 'bad'}):
            wrong = [dict(event) for event in events]
            wrong.insert(9, {'type': 'content_block_delta', 'index': 1, 'delta': delta})
            with self.subTest(delta=delta), self.assertRaises(ProtocolError):
                consume_sse('anthropic', sse(wrong))


class ModelThinkingHTTPTests(unittest.TestCase):
    setUp = ModelProtocolHTTPTests.setUp
    model = ModelProtocolHTTPTests.model

    def test_thinking_options_survive_real_worker_and_affect_identity(self):
        for protocol in ('chat', 'anthropic'):
            with self.subTest(protocol=protocol):
                model = self.model(protocol, thinking='enabled', reasoning_effort='max')
                identities = {self.model(protocol).identity, model.identity,
                              self.model(protocol, thinking='disabled', reasoning_effort='max').identity,
                              self.model(protocol, thinking='enabled', reasoning_effort='low').identity}
                self.assertEqual(len(identities), 4)
                self.assertEqual(model.identity, self.model(protocol, thinking='enabled', reasoning_effort='max').identity)
                if protocol == 'chat':
                    self.server.reply = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'ok',
                        'reasoning_content': 'private'}, 'finish_reason': 'stop'}]}).encode()
                else:
                    self.server.reply = json.dumps({'content': [{'type': 'thinking', 'thinking': 'private', 'signature': 's'},
                        {'type': 'text', 'text': 'ok'}], 'stop_reason': 'end_turn'}).encode()
                message = model.complete([], [], 3, 40)['message']
                model.complete([message], [], 3, 40)
                body = self.server.requests[-1][1]
                self.assertEqual(body['thinking'], {'type': 'enabled'})
                if protocol == 'chat':
                    self.assertEqual(body['reasoning_effort'], 'max')
                    self.assertEqual(body['messages'][0]['reasoning_content'], 'private')
                else:
                    self.assertEqual(body['output_config'], {'effort': 'max'})
                    self.assertEqual(body['messages'][0]['content'], [
                        {'type': 'thinking', 'thinking': 'private', 'signature': 's'}, {'type': 'text', 'text': 'ok'}])


if __name__ == '__main__':
    unittest.main()
