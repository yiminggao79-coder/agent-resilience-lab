"""Actual HTTP barrier tests: callbacks must precede the server's final frames."""
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_lab.model import OpenAIModel, ModelError, MAX_RESPONSE
from agent_lab.protocols import consume_sse
from agent_lab.engine import Engine
from agent_lab.tools import Tools
from agent_lab.store import Store


def event(value, ending='\n'):
    return ('data: ' + json.dumps(value, ensure_ascii=False) + ending + ending).encode()


def fixture(protocol, first='你好', second='🙂'):
    if protocol == 'chat':
        return (event({'choices': [{'index': 0, 'delta': {'reasoning_content': 'PRIVATE_THINKING', 'content': first}}]}),
                event({'choices': [{'index': 0, 'delta': {'content': second}, 'finish_reason': 'stop'}]}) +
                event({'choices': [], 'usage': {'prompt_tokens': 7, 'completion_tokens': 3}}) + b'data: [DONE]\n\n')
    if protocol == 'anthropic':
        prefix = [
            {'type': 'message_start', 'message': {'id': 'm', 'usage': {'input_tokens': 7, 'output_tokens': 0}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'PRIVATE_THINKING'}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'signature_delta', 'signature': 'PRIVATE_SIGNATURE'}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': ''}},
            {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': first}}]
        suffix = [
            {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': second}},
            {'type': 'content_block_stop', 'index': 1},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 3}},
            {'type': 'message_stop'}]
        return b''.join(map(event, prefix)), b''.join(map(event, suffix))
    output = {'type': 'message', 'content': [{'type': 'output_text', 'text': first+second}]}
    return (event({'type': 'response.output_item.added', 'output_index': 0, 'item': {'type': 'message'}}) +
            event({'type': 'response.reasoning_summary_text.delta', 'delta': 'PRIVATE_THINKING'}) +
            event({'type': 'response.output_text.delta', 'output_index': 0, 'delta': first}),
            event({'type': 'response.output_text.delta', 'output_index': 0, 'delta': second}) +
            event({'type': 'response.output_item.done', 'output_index': 0, 'item': output}) +
            event({'type': 'response.completed', 'response': {'id': 'r', 'status': 'completed', 'output': [output],
                  'usage': {'input_tokens': 7, 'output_tokens': 3}}}))


class StreamHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if self.server.streaming else 'application/json')
        self.send_header('Connection', 'close')
        if self.server.chunked:
            self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        def write(data):
            fragments = (bytes([b]) for b in data) if self.server.chunked else [data]
            for part in fragments:
                if self.server.chunked:
                    self.wfile.write(('%x\r\n' % len(part)).encode()+part+b'\r\n')
                else:
                    self.wfile.write(part)
                self.wfile.flush()
        try:
            write(self.server.prefix)
            self.server.first_sent.set()
            if self.server.gated:
                self.server.released_by_callback = self.server.release.wait(2)
            if self.server.hang:
                self.connection.settimeout(2)
                if not self.rfile.read(1):
                    self.server.disconnected.set()
                return
            self.server.final_sent.set()
            write(self.server.suffix)
            if self.server.chunked:
                self.wfile.write(b'0\r\n\r\n')
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.server.disconnected.set()
        finally:
            self.close_connection = True


class IncrementalStreamTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), StreamHandler)
        self.server.daemon_threads = True
        self.server.requests = []
        self.server.prefix, self.server.suffix = fixture('chat')
        self.server.release, self.server.first_sent = threading.Event(), threading.Event()
        self.server.final_sent, self.server.disconnected = threading.Event(), threading.Event()
        self.server.gated = self.server.streaming = True
        self.server.chunked = self.server.hang = False
        self.server.released_by_callback = False
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        env = patch.dict(os.environ, {'LAB_INCREMENTAL_KEY': 'fake-local-only'})
        env.start()
        self.addCleanup(env.stop)

    def model(self, protocol='chat', **options):
        return OpenAIModel('local-contract', 'http://127.0.0.1:%s/v1' % self.server.server_port,
                           'LAB_INCREMENTAL_KEY', True, protocol=protocol,
                           stream=options.pop('stream', True), **options)

    def test_all_protocols_deliver_text_before_final_frames_without_thinking(self):
        for protocol in ('chat', 'anthropic', 'responses'):
            self.server.prefix, self.server.suffix = fixture(protocol)
            self.server.release.clear()
            self.server.final_sent.clear()
            chunks, early = [], []
            def display(text):
                chunks.append(text)
                early.append(not self.server.final_sent.is_set())
                self.server.release.set()
            with self.subTest(protocol=protocol):
                model = self.model(protocol, on_text=display)
                result = model.complete([], [], 4, 50)
                self.assertTrue(self.server.released_by_callback, 'final frames were released without an early text callback')
                self.assertTrue(early[0])
                self.assertEqual(''.join(chunks), '你好🙂')
                self.assertNotIn('PRIVATE', ''.join(chunks))
                self.assertEqual(result['message']['content'], '你好🙂')
                self.assertEqual(result['usage'], {'input_tokens': 7, 'output_tokens': 3})
                self.assertTrue(model.last_text_streamed)

    def test_truncated_stream_may_display_text_but_never_returns_partial_tools(self):
        self.server.prefix = event({'choices': [{'index': 0, 'delta': {'content': 'partial', 'tool_calls': [
            {'index': 0, 'id': 'c', 'function': {'name': 'write_file', 'arguments': '{"path":'}}]}}]})
        self.server.suffix = b''
        chunks = []
        def display(text):
            chunks.append(text)
            self.server.release.set()
        with self.assertRaises(ModelError) as error:
            self.model(on_text=display).complete([], [], 4, 50)
        self.assertEqual(chunks, ['partial'])
        self.assertTrue(error.exception.uncertain)
        self.assertEqual(error.exception.code, 'invalid_model_response')

    def test_callback_exception_isolated_and_identity_unchanged(self):
        def failing(text):
            self.server.release.set()
            raise RuntimeError('PRIVATE_CALLBACK_ERROR')
        model = self.model(on_text=failing)
        self.assertEqual(model.identity, self.model().identity)
        result = model.complete([], [], 4, 50)
        self.assertEqual(result['message']['content'], '你好🙂')
        self.assertFalse(model.last_text_streamed)
        with self.assertRaises(ValueError):
            self.model(on_text='not callable')

    def test_sse_bom_cr_and_chunked_multibyte_utf8_are_incremental(self):
        self.server.chunked = True
        prefix, suffix = fixture('chat')
        self.server.prefix = b'\xef\xbb\xbf' + prefix.replace(b'\n', b'\r')
        self.server.suffix = suffix.replace(b'\n', b'\r\n')
        chunks = []
        def display(text):
            chunks.append(text)
            self.server.release.set()
        result = self.model(on_text=display).complete([], [], 4, 50)
        self.assertEqual(result['message']['content'], '你好🙂')
        self.assertEqual(''.join(chunks), '你好🙂')
        self.assertTrue(self.server.released_by_callback)

    def test_wire_flood_limit_and_absolute_timeout_clean_worker(self):
        self.server.gated = False
        self.server.prefix, self.server.suffix = b':' + b'x' * (MAX_RESPONSE+1), b''
        with self.assertRaises(ModelError) as error:
            self.model(on_text=lambda _: None).complete([], [], 4, 50)
        self.assertEqual(error.exception.code, 'response_too_large')
        self.assertTrue(error.exception.uncertain)
        self.server.prefix, self.server.suffix = fixture('chat')
        self.server.hang = True
        created = []
        original = subprocess.Popen
        def capture(*args, **kwargs):
            child = original(*args, **kwargs)
            created.append(child)
            return child
        start = time.monotonic()
        with patch('agent_lab.model.subprocess.Popen', capture), self.assertRaises(ModelError) as error:
            self.model(on_text=lambda _: None).complete([], [], .35, 50)
        self.assertEqual(error.exception.code, 'model_timeout')
        self.assertTrue(error.exception.uncertain)
        self.assertLess(time.monotonic()-start, 2)
        self.assertTrue(created)
        self.assertTrue(all(child.poll() is not None for child in created))
        self.assertTrue(self.server.disconnected.wait(2))

    def test_parent_ipc_frame_total_and_text_caps_fail_closed(self):
        self.server.gated = False
        self.server.prefix, self.server.suffix = fixture('chat', 'x' * 2048, '')
        for setting in ('MAX_IPC_FRAME', 'MAX_WORKER_OUTPUT', 'MAX_RESPONSE'):
            with self.subTest(setting=setting), patch('agent_lab.model.' + setting, 1024), self.assertRaises(ModelError) as error:
                self.model(on_text=lambda _: None).complete([], [], 3, 50)
            self.assertEqual(error.exception.code, 'response_too_large')
            self.assertTrue(error.exception.uncertain)

    def test_canonical_expansion_cap_applies_even_when_wire_body_fits(self):
        self.server.gated = False
        values = [
            {'type': 'message_start', 'message': {'id': 'm', 'usage': {'input_tokens': 7, 'output_tokens': 0}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'tool_use', 'id': 'c', 'name': 'echo',
                'input': {'value': '"' * (MAX_RESPONSE // 3)}}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 3}},
            {'type': 'message_stop'}]
        self.server.prefix, self.server.suffix = b''.join(map(event, values)), b''
        self.assertLess(len(self.server.prefix), MAX_RESPONSE)
        with self.assertRaises(ModelError) as error:
            self.model('anthropic').complete([], [], 4, 50)
        self.assertEqual(error.exception.code, 'response_too_large')
        self.assertTrue(error.exception.uncertain)

    def test_callback_interrupt_cleans_worker_and_preserves_cancellation(self):
        self.server.gated = False
        self.server.hang = True
        created, original = [], subprocess.Popen
        def capture(*args, **kwargs):
            child = original(*args, **kwargs)
            created.append(child)
            return child
        def interrupt(_):
            raise KeyboardInterrupt()
        with patch('agent_lab.model.subprocess.Popen', capture), self.assertRaises(KeyboardInterrupt):
            self.model(on_text=interrupt).complete([], [], 3, 50)
        self.assertTrue(all(child.poll() is not None for child in created))

    def test_deepseek_credential_name_is_bound_to_official_bases_before_key_read(self):
        for base in ('https://example.invalid/v1', 'http://127.0.0.1:9/v1', 'https://api.deepseek.com.attacker.invalid'):
            with self.subTest(base=base), self.assertRaisesRegex(ValueError, '^unauthorized_api_key_endpoint$'):
                OpenAIModel('contract', base, 'DEEPSEEK_API_KEY', True)
        class NoCredentialRead(dict):
            def get(self, name, *args):
                if name == 'DEEPSEEK_API_KEY':
                    raise AssertionError('must reject endpoint before reading authorized key')
                return super().get(name, *args)
        for official in ('https://api.deepseek.com', 'https://api.deepseek.com/anthropic'):
            model = OpenAIModel('contract', official, 'DEEPSEEK_API_KEY', True)
            model.base_url = 'https://example.invalid'
            with patch('agent_lab.model.os.environ', NoCredentialRead()), self.assertRaises(ModelError) as error:
                model.complete([], [], 1, 10)
            self.assertEqual(error.exception.code, 'unauthorized_api_key_endpoint')
            self.assertFalse(error.exception.uncertain)
        self.assertEqual(self.model().api_key_env, 'LAB_INCREMENTAL_KEY')

    def test_nonstream_complete_remains_compatible(self):
        self.server.gated = self.server.streaming = False
        self.server.prefix = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'legacy'}, 'finish_reason': 'stop'}]}).encode()
        self.server.suffix = b''
        chunks = []
        result = self.model(stream=False, on_text=chunks.append).complete([], [], 3, 50)
        self.assertEqual(result['message']['content'], 'legacy')
        self.assertEqual(chunks, [])

    def test_private_cli_stream_text_stays_out_of_metadata_stdout_and_store_events(self):
        from agent_lab.__main__ import main
        self.server.gated = False
        self.server.prefix, self.server.suffix = fixture('chat', 'PRIVATE_TEXT_DELTA', '')
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(out), redirect_stderr(err):
            db, root = Path(directory)/'tasks.sqlite', Path(directory)/'work'
            root.mkdir()
            code = main(['--db', str(db), 'run', 'text', '--workspace', str(root), '--profile', 'local',
                         '--model', 'local-contract', '--base-url', 'http://127.0.0.1:%s/v1' % self.server.server_port,
                         '--api-key-env', 'LAB_INCREMENTAL_KEY', '--allow-api', '--events', '--stream-text'])
            self.assertEqual(code, 0)
            with Store(db) as store:
                tid = None
                for line in out.getvalue().splitlines():
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict) and 'task_id' in item:
                        tid = item['task_id']
                        break
                self.assertIsNotNone(tid)
                self.assertNotIn('PRIVATE_TEXT_DELTA', json.dumps(store.events(tid)))
        self.assertIn('PRIVATE_TEXT_DELTA', err.getvalue())
        self.assertNotIn('PRIVATE_TEXT_DELTA', out.getvalue())
        self.assertTrue(self.server.requests[-1]['stream'])

    def test_chat_avoids_duplicate_final_only_after_full_successful_display(self):
        from agent_lab.interactive import chat_loop
        self.server.gated = False
        self.server.prefix, self.server.suffix = fixture('chat', 'shown', ' once')
        rendered, chunks = [], []
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory)/'state.sqlite') as store:
            root = Path(directory)/'work'
            root.mkdir()
            model = self.model(on_text=chunks.append)
            engine = Engine(store, Tools(root), model)
            inputs = iter(['hello', '/exit'])
            chat_loop(engine, read=lambda _: next(inputs), write=rendered.append, stream_text=True)
        self.assertEqual(''.join(chunks), 'shown once')
        self.assertNotIn('shown once', rendered)


if __name__ == '__main__':
    unittest.main()
