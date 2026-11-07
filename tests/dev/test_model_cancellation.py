"""Synthetic HTTP barriers for scoped cancellation and trusted-worker ownership."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
import os
import sys
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from agent_lab import model as transport_model
from agent_lab.model import ModelError, OpenAIModel


def sse(value):
    return ('data: ' + json.dumps(value) + '\n\n').encode()


class BarrierHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append((body, self.headers.get('Accept')))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if body['stream'] else 'application/json')
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            self.wfile.write(self.server.prefix)
            self.wfile.flush()
            self.server.started.set()
            self.server.release.wait(5)
            self.server.final_sent.set()
            self.wfile.write(self.server.suffix)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.close_connection = True


class ModelCancellationTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), BarrierHandler)
        self.server.daemon_threads = True
        self.server.requests = []
        self.server.started, self.server.release, self.server.final_sent = threading.Event(), threading.Event(), threading.Event()
        self.server.prefix = sse({'choices': [{'index': 0, 'delta': {'content': 'visible'}}]})
        self.server.suffix = sse({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}) + sse({'choices': [], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}) + b'data: [DONE]\n\n'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.release.set)
        env = patch.dict(os.environ, {'LAB_MODEL_CANCEL_KEY': 'synthetic-cancel-only'})
        env.start()
        self.addCleanup(env.stop)

    def client(self, **kwargs):
        return OpenAIModel('synthetic', 'http://127.0.0.1:%d/v1' % self.server.server_port,
                           'LAB_MODEL_CANCEL_KEY', True, stream=kwargs.pop('stream', True), **kwargs)

    def call_then_cancel(self, client, check=None):
        self.assertTrue(hasattr(client, 'cancellation_scope'), 'missing host cancellation scope')
        cancel, outcome, processes, stdin_closed = threading.Event(), {}, [], []
        actual = subprocess.Popen
        def launch(*args, **kwargs):
            process = actual(*args, **kwargs)
            processes.append(process)
            return process
        def observed_check():
            stdin_closed.append(bool(processes) and processes[0].stdin.closed)
            return check() if check is not None else cancel.is_set()
        def call():
            try:
                with client.cancellation_scope(observed_check):
                    outcome['result'] = client.complete([], [], 4, 30)
            except BaseException as error:
                outcome['error'] = error
        with patch.object(transport_model.subprocess, 'Popen', side_effect=launch):
            thread = threading.Thread(target=call)
            thread.start()
            self.assertTrue(self.server.started.wait(2), 'HTTP worker never reached barrier')
            cancel.set()
            thread.join(1)
            self.assertFalse(thread.is_alive(), 'cancel waited for HTTP final/EOF')
        self.assertFalse(self.server.final_sent.is_set())
        self.assertEqual(len(processes), 1)
        process = processes[0]
        self.assertIsNotNone(process.returncode)
        self.assertTrue(all(pipe.closed for pipe in (process.stdin, process.stdout, process.stderr)))
        self.assertNotIn('result', outcome)
        self.assertTrue(stdin_closed)
        self.assertTrue(all(stdin_closed), 'cancel check ran before complete stdin close')
        error = outcome['error']
        self.assertIsInstance(error, ModelError)
        return error

    def test_public_sse_cancel_before_final_reaps_worker(self):
        shown = []
        error = self.call_then_cancel(self.client(on_text=shown.append))
        self.assertEqual(error.code, 'model_request_cancelled')
        self.assertTrue(error.uncertain)
        self.assertEqual(error.diagnostic, ['phase_ipc', 'cancelled'])
        self.assertFalse(error.retryable)
        self.assertNotIn('synthetic-cancel-only', json.dumps(vars(error)))

    def test_reasoning_only_and_partial_tool_never_need_public_text_to_cancel(self):
        for delta in ({'reasoning_content': 'PRIVATE_REASONING'},
                      {'tool_calls': [{'index': 0, 'id': 'partial', 'function': {'name': 'write_file', 'arguments': '{"path":'}}]}):
            with self.subTest(delta=delta):
                self.server.started.clear()
                self.server.prefix = sse({'choices': [{'index': 0, 'delta': delta}]})
                shown = []
                error = self.call_then_cancel(self.client(on_text=shown.append))
                self.assertEqual(error.code, 'model_request_cancelled')
                self.assertEqual(shown, [])

    def test_scoped_buffered_transport_keeps_provider_stream_false(self):
        self.server.prefix = b'{"choices":'
        self.server.suffix = b'[]}'
        shown = []
        error = self.call_then_cancel(self.client(stream=False, on_text=shown.append))
        self.assertEqual(error.code, 'model_request_cancelled')
        body, accept = self.server.requests[0]
        self.assertFalse(body['stream'])
        self.assertEqual(accept, 'application/json')
        self.assertNotIn('ipc_stream', body)
        self.assertNotIn('owner_fd', body)
        self.assertEqual(shown, [])

    def test_cancel_check_failure_is_sanitized_and_is_not_clean_cancel(self):
        class Opaque(Exception):
            def __str__(self):
                raise AssertionError('must not stringify callback exception')
        def check():
            if self.server.started.is_set():
                raise Opaque()
            return False
        error = self.call_then_cancel(self.client(), check)
        self.assertEqual(error.code, 'model_cancel_check_failed')
        self.assertTrue(error.uncertain)
        self.assertEqual(error.diagnostic, ['phase_ipc', 'worker_failure'])

    def test_scope_preserves_constructor_identity_and_validates_callable(self):
        client = self.client()
        self.assertTrue(hasattr(client, 'cancellation_scope'))
        identity = client.identity
        self.assertNotIn('cancel', str(inspect.signature(OpenAIModel)))
        with client.cancellation_scope(lambda: False):
            self.assertEqual(client.identity, identity)
        self.assertEqual(client.identity, self.client().identity)
        with self.assertRaises(ValueError):
            with client.cancellation_scope(None):
                pass

    def test_nested_scopes_keep_outer_check_and_restore_on_exception(self):
        client = self.client()
        with client.cancellation_scope(lambda: True):
            with client.cancellation_scope(lambda: False):
                with self.assertRaises(ModelError) as caught:
                    client._check_cancelled()
                self.assertEqual(caught.exception.code, 'model_request_cancelled')
        client._check_cancelled()
        with client.cancellation_scope(lambda: False):
            with self.assertRaises(RuntimeError):
                with client.cancellation_scope(lambda: True):
                    raise RuntimeError('scope body only')
            client._check_cancelled()

    def test_same_client_scopes_are_independent_across_threads(self):
        client, gate, outcomes = self.client(), threading.Barrier(2), {}
        def check(name, cancel):
            with client.cancellation_scope(lambda: cancel):
                gate.wait(timeout=2)
                try:
                    client._check_cancelled()
                    outcomes[name] = 'continues'
                except ModelError as error:
                    outcomes[name] = error.code
        threads = [threading.Thread(target=check, args=('a', True)), threading.Thread(target=check, args=('b', False))]
        for thread in threads: thread.start()
        for thread in threads: thread.join(2)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(outcomes, {'a': 'model_request_cancelled', 'b': 'continues'})
        client._check_cancelled()

    def test_owner_pipe_closes_both_fds_on_body_exception_and_launch_failure(self):
        actual_pipe, pairs = os.pipe, []
        def pipe():
            pair = actual_pipe()
            pairs.append(pair)
            return pair
        with patch.object(transport_model.os, 'pipe', side_effect=pipe):
            with self.assertRaises(RuntimeError):
                with transport_model._owner_pipe({}) as (read, environment):
                    self.assertEqual(environment['AGENT_LAB_TRANSPORT_OWNER_FD'], str(read))
                    raise RuntimeError('synthetic body')
        for descriptor in pairs[0]:
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        read_fds = []
        def cannot_launch(*args, **kwargs):
            read_fds.extend(kwargs['pass_fds'])
            raise OSError('synthetic private launcher detail')
        client = self.client()
        with patch.object(transport_model.subprocess, 'Popen', side_effect=cannot_launch):
            with client.cancellation_scope(lambda: False), self.assertRaises(ModelError) as caught:
                client.complete([], [], 2, 10)
        self.assertEqual(caught.exception.code, 'transport_start_failed')
        for descriptor in read_fds:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_worker_owner_eof_exits_before_blocked_buffered_http_finishes(self):
        self.server.prefix = b'{"choices":'
        self.server.suffix = b'[]}'
        owner_read, owner_write = os.pipe()
        environment = {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'AGENT_LAB_TRANSPORT_KEY': 'synthetic-only',
                       'AGENT_LAB_TRANSPORT_OWNER_FD': str(owner_read)}
        process = subprocess.Popen([sys.executable, '-I', '-S', transport_model.__file__],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=environment, pass_fds=(owner_read,), close_fds=True)
        os.close(owner_read)
        payload = {'url': 'http://127.0.0.1:%d/v1/chat/completions' % self.server.server_port,
                   'body': {'stream': False}, 'protocol': 'chat', 'stream': False, 'timeout': 4}
        try:
            process.stdin.write(json.dumps(payload).encode())
            process.stdin.close()
            self.assertTrue(self.server.started.wait(2))
            os.close(owner_write)
            owner_write = None
            self.assertEqual(process.wait(timeout=1), 70)
            self.assertFalse(self.server.final_sent.is_set())
        finally:
            if owner_write is not None: os.close(owner_write)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_scoped_buffered_normal_completion_uses_final_ipc_without_public_callback(self):
        self.server.prefix = json.dumps({'id': 'synthetic', 'choices': [{'message': {'role': 'assistant', 'content': 'done'},
                                        'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 1}}).encode()
        self.server.suffix = b''
        self.server.release.set()
        shown, client = [], self.client(stream=False)
        client.on_text = shown.append
        with client.cancellation_scope(lambda: False):
            result = client.complete([], [], 3, 20)
        self.assertEqual(result['message']['content'], 'done')
        self.assertEqual(result['usage'], {'input_tokens': 3, 'output_tokens': 1})
        self.assertEqual(shown, [])
        self.assertFalse(client.last_text_streamed)
        self.assertFalse(self.server.requests[0][0]['stream'])

    def test_anthropic_and_responses_partial_native_streams_cancel_privately(self):
        from tests.dev.test_incremental_stream import fixture
        for protocol in ('anthropic', 'responses'):
            self.server.started.clear()
            self.server.prefix, self.server.suffix = fixture(protocol)
            shown = []
            with self.subTest(protocol=protocol):
                error = self.call_then_cancel(self.client(protocol=protocol, on_text=shown.append))
                self.assertEqual(error.code, 'model_request_cancelled')
                self.assertNotIn('PRIVATE', ''.join(shown))


if __name__ == '__main__':
    unittest.main()
