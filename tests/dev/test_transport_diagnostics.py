"""Offline failure metadata contracts; all network traffic is synthetic loopback."""
import io
import json
import os
import socket
import socketserver
import ssl
import struct
import subprocess
import sys
import threading
import unittest
import urllib.error
import http.client
from unittest.mock import patch

from agent_lab import model


SECRET = 'PRIVATE_KEY_HEADER_BODY_EXCEPTION'
PARTIAL = ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'content': 'visible', 'tool_calls': [
    {'index': 0, 'id': 'pending', 'function': {'name': 'write_file', 'arguments': '{"path":'}}]}}]}) + '\n\n').encode()


def payload(stream=False):
    return {'url': 'http://127.0.0.1:1/v1/chat/completions', 'protocol': 'chat',
            'body': {}, 'timeout': 1, 'stream': stream}


class DiagnosticUnitTests(unittest.TestCase):
    def test_modelerror_whitelists_labels_status_and_observed_byte_count(self):
        error = model.ModelError('transport_unknown', uncertain=True, http_status=True,
                                 diagnostic=['phase_body', SECRET, 'connection_reset', 'phase_body', {}, True],
                                 response_body_bytes=True)
        self.assertEqual(error.diagnostic, ['phase_body', 'connection_reset'])
        self.assertIsNone(error.http_status)
        self.assertIsNone(error.response_body_bytes)
        self.assertEqual(model.normalize_diagnostics(SECRET), [])
        for status in (-1, 99, 600, 200.0, SECRET):
            self.assertIsNone(model.ModelError('x', http_status=status).http_status)
        for count in (-1, 1.5, SECRET, 10**40):
            self.assertIsNone(model.ModelError('x', response_body_bytes=count).response_body_bytes)
        self.assertEqual(model.ModelError('x', http_status=200, response_body_bytes=0).response_body_bytes, 0)

    def test_open_exception_families_are_fixed_and_do_not_stringify_exceptions(self):
        class OpaqueFailure(Exception):
            def __str__(self):
                raise AssertionError('exception text must never be inspected')
        cases = [(http.client.RemoteDisconnected(SECRET), 'peer_closed', 'transport_unknown'),
                 (ConnectionResetError(SECRET), 'connection_reset', 'transport_unknown'),
                 (ssl.SSLError(SECRET), 'tls', 'transport_unknown'),
                 (http.client.BadStatusLine(SECRET), 'http_framing', 'transport_unknown'),
                 (http.client.IncompleteRead(b'PRIVATE_PARTIAL'), 'incomplete_read', 'transport_unknown'),
                 (TimeoutError(SECRET), 'timeout', 'transport_unknown'),
                 (ConnectionRefusedError(SECRET), 'network', 'transport_unknown'),
                 (OSError(SECRET), 'io', 'transport_unknown'),
                 (urllib.error.URLError(ssl.SSLCertVerificationError(SECRET)), 'tls_certificate', 'tls_verification_failed'),
                 (urllib.error.URLError(socket.gaierror(-2, SECRET)), 'dns', 'dns_failed'),
                 (urllib.error.URLError(SECRET), 'unknown', 'transport_unknown'),
                 (OpaqueFailure(SECRET), 'unknown', 'transport_unknown')]
        for thrown, family, code in cases:
            with self.subTest(family=family), patch.dict(os.environ, {'AGENT_LAB_TRANSPORT_KEY': 'fake-only'}), \
                    patch.object(model.urllib.request, 'build_opener') as factory:
                factory.return_value.open.side_effect = thrown
                result = model.transport(payload())
                self.assertEqual(result['transport_error'], code)
                self.assertEqual(result['diagnostic'], ['phase_open', family])
                self.assertNotIn('http_status', result)
                self.assertNotIn('response_body_bytes', result)
                self.assertNotIn(SECRET, json.dumps(result))

    def test_body_exception_keeps_observed_status_and_delivered_bytes_without_private_headers(self):
        class Response(io.BytesIO):
            headers = {'x-request-id': SECRET}
            status = 200
            chunked = False
            def read1(self, amount):
                if self.tell() == len(PARTIAL):
                    raise ConnectionResetError(SECRET)
                return super().read1(amount)
        with patch.dict(os.environ, {'AGENT_LAB_TRANSPORT_KEY': 'fake-only'}), \
                patch.object(model.urllib.request, 'build_opener') as factory:
            factory.return_value.open.return_value = Response(PARTIAL)
            displayed = []
            result = model.transport(payload(True), displayed.append)
        self.assertEqual(displayed, ['visible'])
        self.assertEqual(result, {'transport_error': 'transport_unknown', 'diagnostic': ['phase_body', 'connection_reset'],
                                  'http_status': 200, 'response_body_bytes': len(PARTIAL)})
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn('completion', result)

    def test_header_processing_failure_does_not_invent_body_bytes(self):
        class Headers(dict):
            def get(self, key, default=None):
                raise ValueError(SECRET)
        class Response(io.BytesIO):
            headers = Headers()
            status = 200
        with patch.dict(os.environ, {'AGENT_LAB_TRANSPORT_KEY': 'fake-only'}), \
                patch.object(model.urllib.request, 'build_opener') as factory:
            factory.return_value.open.return_value = Response()
            result = model.transport(payload())
        self.assertEqual(result, {'transport_error': 'transport_unknown', 'diagnostic': ['phase_headers', 'unknown'],
                                  'http_status': 200})

    def test_parent_drops_untrusted_worker_diagnostic_fields(self):
        fake = subprocess.CompletedProcess([], 0, json.dumps({'transport_error': 'transport_unknown',
            'diagnostic': [SECRET, 'phase_body', 'connection_reset'], 'http_status': 200,
            'response_body_bytes': len(PARTIAL), 'private': SECRET}), '')
        adapter = model.OpenAIModel('synthetic', api_key_env='LAB_DIAGNOSTIC_KEY', allow_api=True)
        with patch.dict(os.environ, {'LAB_DIAGNOSTIC_KEY': 'fake-only'}), \
                patch.object(model.subprocess, 'run', return_value=fake), self.assertRaises(model.ModelError) as caught:
            adapter.complete([], [], 1, 20)
        error = caught.exception
        self.assertEqual(error.diagnostic, ['phase_body', 'connection_reset'])
        self.assertEqual(error.http_status, 200)
        self.assertEqual(error.response_body_bytes, len(PARTIAL))
        self.assertEqual(error.code, 'transport_unknown')
        self.assertTrue(error.uncertain)
        self.assertFalse(error.retryable)
        self.assertNotIn(SECRET, json.dumps(vars(error)))

    def test_nonstream_worker_framing_and_exit_preserve_unknown_response_semantics(self):
        adapter = model.OpenAIModel('synthetic', api_key_env='LAB_DIAGNOSTIC_KEY', allow_api=True)
        for code, family in [(0, 'worker_framing'), (7, 'worker_exit')]:
            fake = subprocess.CompletedProcess([], code, SECRET, SECRET)
            with self.subTest(code=code), patch.dict(os.environ, {'LAB_DIAGNOSTIC_KEY': 'fake-only'}), \
                    patch.object(model.subprocess, 'run', return_value=fake), self.assertRaises(model.ModelError) as caught:
                adapter.complete([], [], 1, 20)
            self.assertEqual(caught.exception.code, 'invalid_model_response')
            self.assertEqual(caught.exception.diagnostic, ['phase_ipc', family])
            self.assertIsNone(caught.exception.response_body_bytes)
            self.assertTrue(caught.exception.uncertain)
            self.assertNotIn(SECRET, json.dumps(vars(caught.exception)))

    def test_real_worker_ipc_failures_are_distinct_and_cannot_expose_output(self):
        scripts = [('import sys; sys.stdin.buffer.read(); print("' + SECRET + '")', 'worker_framing'),
                   ('import sys; sys.stdin.buffer.read()', 'worker_no_final'),
                   ('import sys; sys.stdin.buffer.read(); sys.exit(7)', 'worker_exit')]
        original = subprocess.Popen
        adapter = model.OpenAIModel('synthetic', api_key_env='LAB_DIAGNOSTIC_KEY', allow_api=True, stream=True)
        for script, family in scripts:
            def launch(*args, **kwargs):
                return original([sys.executable, '-I', '-S', '-c', script], **kwargs)
            with self.subTest(family=family), patch.dict(os.environ, {'LAB_DIAGNOSTIC_KEY': 'fake-only'}), \
                    patch.object(model.subprocess, 'Popen', launch), self.assertRaises(model.ModelError) as caught:
                adapter.complete([], [], 2, 20)
            error = caught.exception
            self.assertEqual(error.code, 'invalid_model_response')
            self.assertEqual(error.diagnostic, ['phase_ipc', family])
            self.assertTrue(error.uncertain)
            self.assertFalse(error.retryable)
            self.assertIsNone(error.response_body_bytes)
            self.assertNotIn(SECRET, json.dumps(vars(error)))


class AbruptHandler(socketserver.StreamRequestHandler):
    def handle(self):
        length = 0
        while True:
            line = self.rfile.readline(65536)
            if line in (b'\r\n', b'\n', b''):
                break
            if line.lower().startswith(b'content-length:'):
                length = int(line.split(b':', 1)[1])
        self.rfile.read(length)
        if self.server.mode == 'headers_close':
            return
        if self.server.mode == 'reset':
            self.request.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            self.request.close()
            return
        if self.server.mode == 'chunked_close':
            self.wfile.write(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n')
            self.wfile.write(('%x\r\n' % len(PARTIAL)).encode() + PARTIAL + b'\r\n')
            self.wfile.flush()
            return  # Missing terminating chunk, after a partial executable SSE item.
        status = self.server.status
        body = (SECRET + ' invalid tools').encode()
        self.wfile.write(('HTTP/1.1 %d Synthetic\r\nContent-Length: %d\r\nX-Request-ID: %s\r\nConnection: close\r\n\r\n'
                          % (status, len(body), SECRET)).encode() + body)
        self.wfile.flush()


class DiagnosticHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), AbruptHandler)
        self.server.daemon_threads = True
        self.server.mode, self.server.status = 'headers_close', 200
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        env = patch.dict(os.environ, {'LAB_DIAGNOSTIC_KEY': 'fake-only'})
        env.start()
        self.addCleanup(env.stop)

    def adapter(self, **options):
        return model.OpenAIModel('synthetic', 'http://127.0.0.1:%s/v1' % self.server.server_address[1],
                                 'LAB_DIAGNOSTIC_KEY', True, **options)

    def test_actual_close_before_headers_and_reset_have_observed_open_families(self):
        for mode, family in [('headers_close', 'peer_closed'), ('reset', 'connection_reset')]:
            self.server.mode = mode
            for streaming in (False, True):
                with self.subTest(mode=mode, stream=streaming), self.assertRaises(model.ModelError) as caught:
                    self.adapter(stream=streaming).complete([], [], 3, 20)
                error = caught.exception
                self.assertEqual(error.code, 'transport_unknown')
                self.assertEqual(error.diagnostic, ['phase_open', family])
                self.assertIsNone(error.http_status)
                self.assertIsNone(error.response_body_bytes)
                self.assertTrue(error.uncertain)
                self.assertFalse(error.retryable)

    def test_actual_incomplete_chunk_preserves_partial_text_without_returning_tools(self):
        self.server.mode = 'chunked_close'
        displayed = []
        with self.assertRaises(model.ModelError) as caught:
            self.adapter(stream=True, on_text=displayed.append).complete([], [], 3, 20)
        error = caught.exception
        self.assertEqual(displayed, ['visible'])
        self.assertEqual(error.code, 'transport_unknown')
        self.assertEqual(error.diagnostic, ['phase_body', 'incomplete_chunk'])
        self.assertEqual(error.http_status, 200)
        self.assertEqual(error.response_body_bytes, len(PARTIAL))
        self.assertTrue(error.uncertain)
        self.assertFalse(error.retryable)

    def test_protocol_failure_and_http_error_keep_categories_and_only_safe_metadata(self):
        self.server.mode = 'status'
        for status, code, retryable, uncertain in [(200, 'invalid_model_response', False, True),
                (401, 'authentication_failed', False, False), (429, 'rate_limited', True, False),
                (503, 'service_unavailable', True, False)]:
            self.server.status = status
            with self.subTest(status=status), self.assertRaises(model.ModelError) as caught:
                self.adapter().complete([], [], 3, 20)
            error = caught.exception
            self.assertEqual((error.code, error.retryable, error.uncertain), (code, retryable, uncertain))
            self.assertEqual(error.http_status, status)
            self.assertNotIn(SECRET, json.dumps(vars(error)))
            if status in (200, 401):
                self.assertEqual(error.response_body_bytes, len((SECRET + ' invalid tools').encode()))
            else:
                self.assertIsNone(error.response_body_bytes)
