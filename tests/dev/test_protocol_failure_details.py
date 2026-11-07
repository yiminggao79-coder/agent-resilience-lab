"""Offline reason fidelity; no provider requests, keys or frozen task bodies."""
import io
import json
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import model
from agent_lab.engine import Engine
from agent_lab.protocols import ProtocolError, consume_sse, normalize_response
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.dev import test_model_protocols as protocol_fixtures

sse = protocol_fixtures.sse


SECRET = 'PRIVATE_BODY_AND_EXCEPTION_TEXT'


def anthropic_cut_events(reason='max_tokens'):
    return [{'type': 'message_start', 'message': {'content': [],
             'usage': {'input_tokens': 1, 'output_tokens': 0}}},
            {'type': 'content_block_start', 'index': 0, 'content_block':
             {'type': 'tool_use', 'id': 'c', 'name': 'write_file', 'input': {}}},
            {'type': 'content_block_delta', 'index': 0, 'delta':
             {'type': 'input_json_delta', 'partial_json': '{"path":"cut'}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': reason}},
            {'type': 'message_stop'}]


def limited(protocol):
    if protocol == 'chat':
        return {'choices': [{'message': {'role': 'assistant', 'content': SECRET,
                'tool_calls': [{'id': 'c', 'type': 'function', 'function': {'name': 'write_file',
                'arguments': '{"path":"must-not-exist.txt","content":"partial"}'}}]},
                'finish_reason': 'length'}]}
    if protocol == 'anthropic':
        return {'content': [{'type': 'tool_use', 'id': 'c', 'name': 'write_file',
                'input': {'path': 'must-not-exist.txt', 'content': SECRET}}], 'stop_reason': 'max_tokens'}
    return {'status': 'incomplete', 'error': None, 'output': [],
            'incomplete_details': {'reason': 'max_output_tokens'}}


class ProtocolReasonTests(unittest.TestCase):
    def test_anthropic_truncated_tool_json_retains_explicit_limit_reason(self):
        for reason, expected in [('max_tokens', 'output_token_limit'), ('tool_use', 'invalid_tool_arguments')]:
            with self.subTest(reason=reason), self.assertRaises(ProtocolError) as caught:
                consume_sse('anthropic', sse(anthropic_cut_events(reason)))
            self.assertEqual(caught.exception.args, (expected,))

    def test_explicit_token_limits_are_distinct_from_missing_completion(self):
        for protocol in ('chat', 'anthropic', 'responses'):
            with self.subTest(protocol=protocol), self.assertRaises(ProtocolError) as caught:
                normalize_response(protocol, limited(protocol))
            self.assertEqual(caught.exception.args, ('output_token_limit',))
        with self.assertRaises(ProtocolError) as caught:
            consume_sse('chat', sse([{'choices': [{'index': 0, 'delta': {'content': SECRET}}]}]))
        self.assertEqual(caught.exception.args, ('incomplete_stream',))

    def test_stream_token_limit_does_not_return_partial_calls(self):
        fixtures = [('chat', sse([{'choices': [{'index': 0, 'delta': {'content': SECRET},
                     'finish_reason': 'length'}]}], done=True)),
                    ('anthropic', sse([{'type': 'message_start', 'message': {'content': [],
                     'usage': {'input_tokens': 1, 'output_tokens': 0}}},
                     {'type': 'message_delta', 'delta': {'stop_reason': 'max_tokens'}},
                     {'type': 'message_stop'}])),
                    ('responses', sse([{'type': 'response.incomplete', 'response': limited('responses')}]))]
        for protocol, lines in fixtures:
            with self.subTest(protocol=protocol), self.assertRaises(ProtocolError) as caught:
                consume_sse(protocol, lines)
            self.assertEqual(caught.exception.args, ('output_token_limit',))

    def test_tls_subclasses_are_classified_without_exception_text(self):
        for thrown, label in [(ssl.SSLEOFError(SECRET), 'tls_eof'),
                              (ssl.SSLCertVerificationError(SECRET), 'tls_certificate')]:
            with self.subTest(label=label), patch.dict(os.environ, {'AGENT_LAB_TRANSPORT_KEY': 'fake'}), \
                    patch.object(model.urllib.request, 'build_opener') as factory:
                factory.return_value.open.side_effect = thrown
                result = model.transport({'url': 'https://invalid.example', 'protocol': 'chat',
                         'body': {}, 'timeout': 1, 'stream': False})
            self.assertEqual(result['diagnostic'], ['phase_open', label])
            self.assertNotIn(SECRET, json.dumps(result))

    def test_unlisted_protocol_exception_detail_is_not_a_public_label(self):
        class Response(io.BytesIO):
            headers = {}
            status = 200
        with patch.dict(os.environ, {'AGENT_LAB_TRANSPORT_KEY': 'fake'}), \
                patch.object(model.urllib.request, 'build_opener') as factory, \
                patch.object(model._protocols, 'normalize_response',
                             side_effect=model._protocols.ProtocolError(SECRET)):
            factory.return_value.open.return_value = Response(b'{}')
            result = model.transport({'url': 'https://invalid.example', 'protocol': 'chat',
                     'body': {}, 'timeout': 1, 'stream': False})
        self.assertEqual(result['diagnostic'], ['phase_body', 'protocol'])
        self.assertNotIn(SECRET, json.dumps(result))


class ProtocolReasonHTTPTests(unittest.TestCase):
    setUp = protocol_fixtures.ModelProtocolHTTPTests.setUp
    model = protocol_fixtures.ModelProtocolHTTPTests.model

    def test_real_worker_reports_three_protocol_buffered_limits(self):
        for protocol in ('chat', 'anthropic', 'responses'):
            self.server.reply = json.dumps(limited(protocol)).encode()
            with self.subTest(protocol=protocol), self.assertRaises(model.ModelError) as caught:
                self.model(protocol).complete([{'role': 'user', 'content': 'exercise'}], [], 3, 20)
            self.assertEqual(caught.exception.code, 'invalid_model_response')
            self.assertTrue(caught.exception.uncertain)
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(caught.exception.diagnostic, ['phase_body', 'protocol', 'output_token_limit'])
            self.assertNotIn(SECRET, json.dumps(vars(caught.exception)))

    def test_real_worker_distinguishes_incomplete_json_and_schema(self):
        for raw, stream, detail in [
                (''.join(sse([{'choices': [{'index': 0, 'delta': {'content': SECRET}}]}])).encode(), True, 'incomplete_stream'),
                (b'data: {broken-private\n\n', True, 'invalid_sse_json'),
                (b'{broken-private', False, 'json_parse'),
                (b'{"choices":[]}', False, 'invalid_response')]:
            self.server.reply = raw
            with self.subTest(detail=detail), self.assertRaises(model.ModelError) as caught:
                self.model(stream=stream).complete([], [], 3, 20)
            self.assertEqual(caught.exception.diagnostic, ['phase_body', 'protocol', detail])
            self.assertTrue(caught.exception.uncertain)
            self.assertNotIn('broken-private', json.dumps(vars(caught.exception)))

    def test_engine_preserves_limit_reason_without_executing_partial_write(self):
        chat = (''.join(sse([{'choices': [{'index': 0, 'delta': {
             'tool_calls': [{'index': 0, 'id': 'c', 'function': {'name': 'write_file',
             'arguments': '{"path":"must-not-exist.txt","content":"partial"}'}}]},
             'finish_reason': 'length'}]}], done=True))).encode()
        for protocol, reply in [('chat', chat), ('anthropic', ''.join(sse(anthropic_cut_events())).encode())]:
            self.server.reply = reply
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)/'project';root.mkdir();db = Path(temp)/'state.sqlite'
                with Store(db) as store:
                    engine = Engine(store, Tools(root), self.model(protocol, stream=True))
                    task = engine.create('write a file');state = engine.run(task)
                    self.assertEqual(state['phase'], 'needs_confirmation')
                    self.assertEqual(state['metrics']['tool_attempts'], 0)
                    self.assertIsNone(state['metrics']['input_tokens'])
                with Store(db) as store:
                    event = next(e for e in store.events(task) if e['kind'] == 'model_error')
                    self.assertEqual(event['diagnostic'], 'phase_body,protocol,output_token_limit')
                    self.assertNotIn(SECRET, json.dumps(store.events(task)))
                self.assertFalse((root/'must-not-exist.txt').exists())
