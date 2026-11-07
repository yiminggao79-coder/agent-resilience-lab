"""Real subprocess tests for trusted MCP stdio, without network or credentials."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

try:
    from agent_lab.mcp import MCPClient, MCPError
except ImportError:
    MCPClient = MCPError = None

SERVER = Path(__file__).resolve().parents[1] / 'fixtures' / 'mcp_server.py'


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def client(self, mode='normal', **options):
        self.assertIsNotNone(MCPClient, 'real MCP stdio client missing')
        command = (sys.executable, '-I', '-S', str(SERVER), mode, str(self.root/'requests'), str(self.root/'marker'))
        client = MCPClient(command, self.root, timeout=options.pop('timeout', 3), **options)
        self.addCleanup(client.close)
        return client

    def started(self, mode='normal', **options):
        client = self.client(mode, **options)
        client.start()
        client.list_tools()
        return client

    def requests(self):
        return [json.loads(line) for line in (self.root/'requests').read_text().splitlines()]

    def test_real_initialize_paginated_discovery_and_structured_call(self):
        client = self.client()
        self.assertFalse((self.root/'requests').exists())
        with client:
            tools = client.list_tools()
            self.assertEqual([t['name'] for t in tools], ['echo', 'inspect', 'mutate'])
            result = client.call_tool('echo', {'number': 7})
            self.assertEqual(result['structuredContent'], {'number': 7})
            self.assertEqual(result['content'][0]['text'], 'real local stdio result\nsecond line')
            self.assertFalse(result['isError'])
            self.assertTrue(client.call_tool('echo', {'fail': True})['isError'])
        messages = self.requests()
        self.assertEqual([m.get('method') for m in messages],
                         ['initialize', 'notifications/initialized', 'tools/list', 'tools/list', 'tools/call', 'tools/call'])
        self.assertEqual([m['id'] for m in messages if 'id' in m], [1, 2, 3, 4, 5])
        self.assertEqual(messages[3]['params'], {'cursor': 'page2'})
        self.assertGreater(client.stderr_bytes, 0)
        with self.assertRaises(MCPError):
            client.call_tool('echo', {})

    def test_version_negotiation_and_capability_requirements(self):
        client = self.client('old_version')
        client.start()
        self.assertEqual(client.protocol_version, '2025-06-18')
        client.close()
        for mode, operation, code in [('bad_version', 'start', 'unsupported_protocol_version'),
                                      ('no_tools', 'list_tools', 'tools_not_supported')]:
            client = self.client(mode)
            if operation != 'start':
                client.start()
            with self.subTest(mode=mode), self.assertRaises(MCPError) as error:
                getattr(client, operation)()
            self.assertEqual(error.exception.code, code)
            client.close()

    def test_server_notifications_and_ping_do_not_become_results(self):
        for mode in ('notifications', 'server_request'):
            client = self.started(mode)
            self.assertEqual(client.call_tool('echo', {'ok': 1})['structuredContent'], {'ok': 1})
            client.close()

    def test_protocol_failures_are_sanitized_and_effects_are_uncertain(self):
        for mode in ('eof', 'partial', 'malformed', 'wrongid', 'boolean_id', 'double_envelope', 'rpc_error', 'bad_result', 'die', 'duplicate_response', 'duplicate_keys'):
            client = self.started(mode)
            with self.subTest(mode=mode), self.assertRaises(MCPError) as caught:
                client.call_tool('echo', {})
            error = caught.exception
            self.assertTrue(error.uncertain)
            self.assertNotIn('PRIVATE', str(error))
            self.assertNotIn('SECRET', str(error))
            if mode == 'rpc_error':
                self.assertEqual(error.rpc_code, -32603)
            with self.assertRaises(MCPError):
                client.call_tool('echo', {})
            client.close()

    def test_real_timeout_does_not_reconnect_or_replay_mutating_call(self):
        client = self.started('effect_timeout', timeout=.4)
        start = time.monotonic()
        with self.assertRaises(MCPError) as caught:
            client.call_tool('mutate', {})
        self.assertEqual(caught.exception.code, 'request_timeout')
        self.assertTrue(caught.exception.uncertain)
        self.assertLess(time.monotonic()-start, 2)
        with self.assertRaises(MCPError):
            client.start()
        self.assertEqual((self.root/'marker').read_text(), 'effect\n')
        self.assertEqual(sum(m.get('method') == 'tools/call' for m in self.requests()), 1)

    def test_stdout_stderr_and_notification_floods_are_bounded(self):
        for mode in ('stdout_flood', 'stderr_flood', 'notification_flood'):
            client = self.started(mode, max_bytes=4096)
            start = time.monotonic()
            with self.subTest(mode=mode), self.assertRaises(MCPError) as caught:
                client.call_tool('echo', {})
            self.assertEqual(caught.exception.code, 'output_limit')
            self.assertTrue(caught.exception.uncertain)
            self.assertLess(time.monotonic()-start, 2)
            client.close()

    def test_discovery_rejects_cycles_invalid_schemas_and_item_overflow(self):
        for mode, options in [('repeat_cursor', {}), ('bad_tool', {}), ('too_many_tools', {'max_tools': 3})]:
            client = self.client(mode, **options)
            client.start()
            with self.subTest(mode=mode), self.assertRaises(MCPError):
                client.list_tools()
            client.close()

    def test_environment_requires_explicit_values_and_allowlist(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'SYNTHETIC_SENTINEL', 'SYNTHETIC_ALLOWED': 'ambient'}):
            client = self.started(env={'SYNTHETIC_ALLOWED': 'explicit'}, env_allowlist=('SYNTHETIC_ALLOWED',))
            result = client.call_tool('inspect', {})['structuredContent']
        self.assertEqual(result['value'], 'explicit')
        self.assertNotIn('DEEPSEEK_API_KEY', result['env_keys'])
        self.assertNotIn('HOME', result['env_keys'])
        with self.assertRaises(ValueError):
            self.client(env={'DEEPSEEK_API_KEY': 'fake'})

    def test_unlisted_tool_and_bad_arguments_never_reach_server(self):
        client = self.started()
        before = len(self.requests())
        for name, args in [('unlisted', {}), ('echo', []), ('echo', {'value': float('nan')})]:
            with self.subTest(name=name), self.assertRaises((MCPError, ValueError)):
                client.call_tool(name, args)
        self.assertEqual(len(self.requests()), before)

    def test_progress_notifications_never_extend_request_deadline(self):
        client = self.started('notification_timeout', timeout=.3)
        start = time.monotonic()
        with self.assertRaises(MCPError) as caught:
            client.call_tool('echo', {})
        self.assertEqual(caught.exception.code, 'request_timeout')
        self.assertTrue(caught.exception.uncertain)
        self.assertLess(time.monotonic()-start, 2)

    def test_list_changed_requires_explicit_catalog_refresh(self):
        client = self.started('notifications')
        client.call_tool('echo', {})
        with self.assertRaises(MCPError) as caught:
            client.call_tool('echo', {})
        self.assertEqual(caught.exception.code, 'tool_not_discovered')
        self.assertFalse(caught.exception.uncertain)
        client.list_tools()
        self.assertEqual(client.call_tool('echo', {'after': 'refresh'})['structuredContent'], {'after': 'refresh'})

    def test_page_limit_is_bounded_and_config_is_validated_before_launch(self):
        client = self.client(max_pages=1)
        client.start()
        with self.assertRaises(MCPError) as caught:
            client.list_tools()
        self.assertEqual(caught.exception.code, 'tool_catalog_limit')
        for options in ({'timeout': float('inf')}, {'timeout': True}, {'max_bytes': 0},
                        {'max_pages': 0}, {'max_tools': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.client(**options)
        with self.assertRaises(ValueError):
            MCPClient('python shell text', self.root)

    def test_calls_are_serialized_with_unique_ids(self):
        client = self.started()
        results, errors = [], []
        def invoke(i):
            try:
                results.append(client.call_tool('echo', {'n': i})['structuredContent']['n'])
            except Exception as error:
                errors.append(type(error).__name__)
        threads = [threading.Thread(target=invoke, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [0, 1, 2, 3])

    def test_close_kills_process_group_even_after_leader_death(self):
        for mode in ('spawn', 'parent_dies'):
            marker = self.root/'marker'
            marker.unlink(missing_ok=True)
            client = self.started(mode, timeout=.5)
            if mode == 'parent_dies':
                with self.assertRaises(MCPError) as caught:
                    client.call_tool('echo', {})
                self.assertTrue(caught.exception.uncertain)
            else:
                client.call_tool('echo', {})
            client.close()
            previous = marker.read_text()
            time.sleep(.1)
            self.assertEqual(marker.read_text(), previous)
        client = self.started('ignore_term')
        start = time.monotonic()
        client.close()
        self.assertLess(time.monotonic()-start, 2)


if __name__ == '__main__':
    unittest.main()
