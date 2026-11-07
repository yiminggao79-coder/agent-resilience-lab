import io
import json
import unittest
from unittest.mock import patch
from agent_lab import model


class TLSConfigurationTests(unittest.TestCase):
    def test_explicit_trust_bundle_reaches_https_handler_without_disabling_checks(self):
        # Real handler construction with system CA, replacing only remote I/O.
        captured = []
        original = model.urllib.request.build_opener
        def opener(*handlers):
            captured.extend(handlers)
            class Offline:
                def open(self, request, timeout):
                    class Response(io.BytesIO):
                        headers = {}
                    return Response(json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'ok'},
                                                              'finish_reason': 'stop'}]}).encode())
            return Offline()
        with patch.dict(model.os.environ, {'AGENT_LAB_TRANSPORT_KEY': 'fake'}), patch.object(model.urllib.request, 'build_opener', opener):
            result = model.transport({'protocol': 'chat', 'url': 'https://api.deepseek.com/chat/completions',
                                      'body': {}, 'timeout': 1, 'stream': False, 'ca_bundle': '/etc/ssl/cert.pem'})
        self.assertIn('completion', result)
        contexts = [h._context for h in captured if isinstance(h, model.urllib.request.HTTPSHandler)]
        self.assertEqual(len(contexts), 1, 'explicit CA not used')
        import ssl
        self.assertEqual(contexts[0].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(contexts[0].check_hostname)

    def test_missing_explicit_trust_store_is_sanitized_and_no_network(self):
        with patch.dict(model.os.environ, {'AGENT_LAB_TRANSPORT_KEY': 'fake'}), patch.object(model.urllib.request, 'build_opener') as network:
            result = model.transport({'protocol': 'chat', 'url': 'https://api.deepseek.com/chat/completions',
                                      'body': {}, 'timeout': 1, 'stream': False, 'ca_bundle': '/nonexistent/ca.pem'})
        self.assertEqual(result, {'transport_error': 'tls_configuration_error', 'diagnostic': ['phase_tls_setup', 'tls']})
        network.assert_not_called()

    def test_default_identity_matches_f1ddc89_persisted_configuration(self):
        import hashlib
        # Frozen old configuration format: only non-default new capabilities may
        # change identity, otherwise pre-existing resumable work is stranded.
        old = {'base_url':'https://api.example.com/v1','model':'m','api_key_env':'KEY',
               'protocol':'chat','stream':False,'token_parameter':'max_completion_tokens','parallel_parameter':True}
        old_id = hashlib.sha256(json.dumps(old,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(model.OpenAIModel('m','https://api.example.com/v1','KEY').identity,old_id)
