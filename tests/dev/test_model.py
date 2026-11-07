from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import subprocess
import sys
import unittest
from unittest.mock import patch
from agent_lab.model import OpenAIModel, ModelError


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append((self.path, body, self.headers.get('Authorization')))
        self.send_response(self.server.status)
        if self.server.status == 302:
            self.send_header('Location', '/redirect-target')
        self.end_headers()
        time.sleep(self.server.delay)
        try:
            self.wfile.write(json.dumps(self.server.reply).encode())
        except BrokenPipeError:
            pass


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.requests = []
        self.server.status = 200
        self.server.delay = 0
        self.server.reply = {'id': 'req_test', 'choices': [{'message': {'role': 'assistant', 'content': 'ok'}}],
                             'usage': {'prompt_tokens': 10, 'completion_tokens': 3}}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.env = patch.dict(os.environ, {'LAB_TEST_KEY': 'fake-contract-key'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.model = OpenAIModel('contract-model', 'http://127.0.0.1:%d/v1' % self.server.server_port,
                                 api_key_env='LAB_TEST_KEY', allow_api=True)

    def test_real_http_payload_tools_usage_and_key_environment(self):
        result = self.model.complete([{'role': 'user', 'content': 'hello'}], [], 3, 100)
        path, body, auth = self.server.requests[0]
        self.assertEqual(path, '/v1/chat/completions')
        self.assertEqual(body['model'], 'contract-model')
        self.assertEqual(body['max_completion_tokens'], 100)
        self.assertFalse(body['parallel_tool_calls'])
        self.assertEqual(auth, 'Bearer fake-contract-key')
        self.assertEqual(result['usage'], {'input_tokens': 10, 'output_tokens': 3})

    def test_http_status_classification(self):
        for status, retryable in ((429, True), (503, True), (401, False)):
            self.server.status = status
            with self.subTest(status=status), self.assertRaises(ModelError) as error:
                self.model.complete([], [], 3, 100)
            self.assertEqual(error.exception.retryable, retryable)

    def test_no_network_without_explicit_enable(self):
        model = OpenAIModel('contract-model', self.model.base_url, api_key_env='LAB_TEST_KEY')
        with self.assertRaises(ModelError):
            model.complete([], [], 3, 100)
        self.assertEqual(self.server.requests, [])

    def test_remote_http_and_credential_urls_rejected(self):
        for url in ('http://example.com/v1', 'https://secret@example.com/v1', 'file:///tmp/x', 'https://example.com/?key=x'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OpenAIModel('m', url)

    def test_invalid_json_shape_is_unknown_not_retryable(self):
        self.server.reply = {'not': 'a completion'}
        with self.assertRaises(ModelError) as error:
            self.model.complete([], [], 3, 100)
        self.assertTrue(error.exception.uncertain)
        self.assertFalse(error.exception.retryable)

    def test_http_wall_timeout_and_redirect_refusal(self):
        self.server.delay = 0.5
        started = time.monotonic()
        with self.assertRaises(ModelError) as error:
            self.model.complete([], [], 0.15, 100)
        self.assertTrue(error.exception.uncertain)
        self.assertLess(time.monotonic() - started, 1)
        self.server.delay = 0
        self.server.status = 302
        with self.assertRaises(ModelError):
            self.model.complete([], [], 3, 100)
        self.assertTrue(all(request[0] == '/v1/chat/completions' for request in self.server.requests))

    def test_malformed_usage_is_unmeasured_instead_of_crashing(self):
        for usage in ([], 3, 'bad', {'prompt_tokens': -1, 'completion_tokens': 2}):
            self.server.reply['usage'] = usage
            with self.subTest(usage=usage):
                self.assertIsNone(self.model.complete([], [], 3, 100)['usage'])

    def test_provider_body_cannot_control_retry_policy_or_public_error_code(self):
        self.server.reply = {'error': 'server_secret_token', 'retryable': True, 'uncertain': False}
        with self.assertRaises(ModelError) as error:
            self.model.complete([], [], 3, 100)
        self.assertEqual(error.exception.code, 'invalid_model_response')
        self.assertFalse(error.exception.retryable)
        self.assertTrue(error.exception.uncertain)
