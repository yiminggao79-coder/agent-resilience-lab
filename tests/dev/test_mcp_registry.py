"""Agent integration with an actual trusted synthetic stdio server; no APIs."""
from contextlib import redirect_stdout, redirect_stderr
import copy
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from agent_lab.tools import ToolError, UncertainEffect
from tests.support import ScriptedModel, tool, final
try:
    from agent_lab.registry import MCPToolRegistry
except ImportError:
    MCPToolRegistry = None

SERVER = Path(__file__).resolve().parents[1]/'fixtures'/'mcp_server.py'


class MCPRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home/'workspace'
        self.root.mkdir()
        self.trusted = self.home/'trusted'
        self.trusted.mkdir()
        self.server = self.trusted/'server.py'
        shutil.copyfile(SERVER, self.server)
        self.config = self.trusted/'mcp.json'
        self.store = Store(self.home/'state'/'tasks.sqlite')
        self.addCleanup(self.store.close)
        self.write_config()

    def write_config(self, mode='normal', **extra):
        self.config.write_text(json.dumps({'servers': [{'name': 'synthetic',
            'command': [sys.executable, '-I', '-S', str(self.server), mode,
                        str(self.trusted/'requests'), str(self.trusted/'marker')],
            'cwd': str(self.trusted), **extra}]}))

    def registry(self):
        self.assertIsNotNone(MCPToolRegistry, 'MCP agent registry missing')
        registry = MCPToolRegistry(CodingTools(self.root), self.config)
        registry.__enter__()
        self.addCleanup(registry.close)
        return registry

    @staticmethod
    def wire(registry, original='echo'):
        return next(t['name'] for t in registry.manifest['tools'] if t['tool'] == original)

    def requests(self):
        return [json.loads(line) for line in (self.trusted/'requests').read_text().splitlines()]

    def approve(self, engine, tid):
        request = engine.approval_request(tid)
        engine.decide_approval(tid, request['request_id'], request['digest'], True)
        return request

    def test_agent_approval_precedes_real_call_and_feedback_reaches_model(self):
        registry = self.registry()
        model = ScriptedModel([tool(self.wire(registry), {'number': 7}), final('used feedback')])
        engine = Engine(self.store, registry, model)
        tid = engine.create('Use the explicitly trusted extension', Limits(tool_timeout=2))
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'awaiting_approval')
        self.assertFalse(any(m.get('method') == 'tools/call' for m in self.requests()))
        request = self.approve(engine, tid)
        self.assertEqual(request['action']['server'], 'synthetic')
        self.assertEqual(request['action']['tool'], 'echo')
        self.assertEqual(request['action']['arguments'], {'number': 7})
        self.assertEqual(request['action']['authority'], 'external_trusted_process')
        self.assertEqual(request['action']['command'][0], sys.executable)
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'completed')
        feedback = json.loads(model.seen[-1][-1]['content'])
        self.assertTrue(feedback['ok'])
        self.assertEqual(feedback['structuredContent'], {'number': 7})
        self.assertNotIn('PRIVATE_SYNTHETIC_STDERR', json.dumps(self.store.events(tid)))
        self.assertEqual(sum(m.get('method') == 'tools/call' for m in self.requests()), 1)

    def test_tool_error_is_private_structured_feedback_without_unknown_replay(self):
        registry = self.registry()
        model = ScriptedModel([tool(self.wire(registry), {'fail': True}), final('handled error')])
        engine = Engine(self.store, registry, model)
        tid = engine.create('call')
        engine.run(tid)
        self.approve(engine, tid)
        self.assertEqual(engine.run(tid)['phase'], 'completed')
        feedback = json.loads(model.seen[-1][-1]['content'])
        self.assertFalse(feedback['ok'])
        self.assertTrue(feedback['isError'])
        self.assertEqual(feedback['error'], 'mcp_tool_error')

    def test_configuration_or_catalog_change_rejects_resume_and_adding_removing_registry(self):
        registry = self.registry()
        engine = Engine(self.store, registry, ScriptedModel([final()]))
        tid = engine.create('call')
        engine.run(tid)
        registry.close()
        self.server.write_text(self.server.read_text().replace('synthetic local tool', 'changed trusted tool'))
        changed = self.registry()
        with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
            Engine(self.store, changed, ScriptedModel([])).run(tid)
        with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
            Engine(self.store, CodingTools(self.root), ScriptedModel([])).run(tid)
        plain = Engine(self.store, CodingTools(self.root), ScriptedModel([])).create('plain')
        with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
            Engine(self.store, changed, ScriptedModel([])).run(plain)
        changed.close()
        self.write_config(timeout=2)
        config_changed = self.registry()
        self.assertNotEqual(registry.identity, config_changed.identity)

    def test_cached_catalog_never_accumulates_engine_history_tools(self):
        registry = self.registry()
        engine = Engine(self.store, registry, ScriptedModel([]))
        first, second = engine._schemas(), engine._schemas()
        echo = next(s['function'] for s in first if s['function']['name'] == self.wire(registry))
        self.assertIn('synthetic/echo', echo['description'])
        self.assertEqual(sum(t['function']['name'] == 'history_lookup' for t in first), 1)
        self.assertEqual(first, second)
        self.assertFalse(any(t['function']['name'] == 'history_lookup' for t in registry.schemas()))
        first[0]['function']['description'] = 'mutated local copy'
        self.assertNotEqual(first, engine._schemas())

    def test_strict_intent_and_approval_digest_reject_tampering(self):
        registry = self.registry()
        name = self.wire(registry)
        for args in ([], {'n': float('nan')}):
            with self.assertRaises(ToolError):
                registry.prepare(name, args)
        intent = registry.prepare(name, {'number': 7})
        self.assertTrue(intent['requires_approval'])
        self.assertEqual(intent['policy'], 'unsafe')
        changed = copy.deepcopy(intent)
        changed['args']['number'] = 8
        with self.assertRaises(ToolError):
            registry.execute(changed, 1)
        self.assertFalse(any(m.get('method') == 'tools/call' for m in self.requests()))
        with self.assertRaises(UncertainEffect):
            registry.recover(intent, 1)

    def test_intent_binding_preserves_json_boolean_types(self):
        registry = self.registry()
        intent = registry.prepare(self.wire(registry), {})
        intent['requires_approval'] = 1
        with self.assertRaises(ToolError):
            registry.execute(intent, 1)
        self.assertFalse(any(m.get('method') == 'tools/call' for m in self.requests()))

    def test_partial_startup_failure_closes_all_started_processes(self):
        self.assertIsNotNone(MCPToolRegistry, 'MCP agent registry missing')
        value = json.loads(self.config.read_text())
        second = copy.deepcopy(value['servers'][0])
        second['name'] = 'bad-version'
        second['command'][4] = 'bad_version'
        value['servers'].append(second)
        self.config.write_text(json.dumps(value))
        registry = MCPToolRegistry(CodingTools(self.root), self.config)
        with self.assertRaises(ValueError):
            registry.__enter__()
        self.assertEqual(len(registry._clients), 2)
        self.assertTrue(all(client._process.poll() is not None for client in registry._clients.values()))

    def test_unknown_call_enters_confirmation_and_is_never_replayed_on_resume(self):
        self.write_config('effect_timeout', timeout=.25)
        registry = self.registry()
        model = ScriptedModel([tool(self.wire(registry, 'mutate'), {}), final()])
        engine = Engine(self.store, registry, model)
        tid = engine.create('call', Limits(tool_timeout=.25))
        engine.run(tid)
        self.approve(engine, tid)
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'needs_confirmation')
        self.assertTrue(state['outcome_unresolved'])
        self.assertEqual(engine.run(tid)['phase'], 'needs_confirmation')
        self.assertEqual((self.trusted/'marker').read_text(), 'effect\n')
        self.assertEqual(sum(m.get('method') == 'tools/call' for m in self.requests()), 1)

    def test_lost_acknowledgement_recovers_without_reissuing_external_call(self):
        registry = self.registry()
        model = ScriptedModel([tool(self.wire(registry), {'number': 1}), final()])
        def crash(event, state):
            if event == 'after_tool_effect':
                raise KeyboardInterrupt()
        engine = Engine(self.store, registry, model, hook=crash)
        tid = engine.create('call')
        engine.run(tid)
        self.approve(engine, tid)
        state = engine.run(tid)
        # Simulate a crash snapshot retaining the durable running intent.
        state['phase'], state['reason'] = 'tool_pending', None
        self.store.save(state, 'synthetic_crash', {})
        registry.close()
        restarted = self.registry()
        state = Engine(self.store, restarted, ScriptedModel([])).run(tid)
        self.assertEqual(state['phase'], 'needs_confirmation')
        self.assertEqual(sum(m.get('method') == 'tools/call' for m in self.requests()), 1)

    def test_ambient_api_keys_are_not_forwarded_and_close_quiesces_local_group(self):
        self.write_config('spawn')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'SYNTHETIC', 'DEEPSEEK_API_KEY': 'SYNTHETIC',
                                     'ANTHROPIC_API_KEY': 'SYNTHETIC'}):
            registry = self.registry()
            intent = registry.prepare(self.wire(registry, 'inspect'), {})
            result = registry.execute(intent, 2)
        self.assertFalse({'OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'ANTHROPIC_API_KEY'} & set(result['structuredContent']['env_keys']))
        registry.close()
        before = (self.trusted/'marker').read_text()
        time.sleep(.1)
        self.assertEqual((self.trusted/'marker').read_text(), before)

    def test_trusted_config_rejects_workspace_links_unknown_keys_and_environment(self):
        self.assertIsNotNone(MCPToolRegistry, 'MCP agent registry missing')
        inside = self.root/'config.json'
        inside.write_text(self.config.read_text())
        for path in (inside, self.trusted/'link.json'):
            if path.name == 'link.json':
                path.symlink_to(self.config)
            with self.subTest(path=path), self.assertRaises((ValueError, ToolError)):
                with MCPToolRegistry(CodingTools(self.root), path):
                    pass
        for extra in ({'env': {}}, {'api_key': 'fake'}, {'unknown': True}, {'cwd': str(self.root)}):
            self.write_config(**extra)
            with self.subTest(extra=extra), self.assertRaises((ValueError, ToolError)):
                with MCPToolRegistry(CodingTools(self.root), self.config):
                    pass
        self.write_config()
        hardlink = self.trusted/'hardlink.json'
        os.link(self.config, hardlink)
        with self.assertRaises((ValueError, ToolError)):
            with MCPToolRegistry(CodingTools(self.root), hardlink):
                pass

    def test_workspace_executable_alias_cannot_become_trusted_host_command(self):
        self.assertIsNotNone(MCPToolRegistry, 'MCP agent registry missing')
        alias = self.root/'host-python'
        alias.symlink_to(Path(sys.executable).resolve())
        value = json.loads(self.config.read_text())
        value['servers'][0]['command'][0] = str(alias)
        self.config.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            MCPToolRegistry(CodingTools(self.root), self.config)
        self.assertFalse((self.trusted/'requests').exists())

    def test_cli_error_and_chat_exit_close_actual_stdio_clients(self):
        self.assertIsNotNone(MCPToolRegistry, 'MCP agent registry missing')
        from agent_lab import registry as module
        from agent_lab.__main__ import main
        created = []
        original = MCPToolRegistry
        def factory(*args, **kwargs):
            value = original(*args, **kwargs)
            created.append(value)
            return value
        common = ['--db', str(self.home/'cli.sqlite'), 'run', 'x', '--workspace', str(self.root),
                  '--model', 'fake', '--mcp-config', str(self.config)]
        with patch.object(module, 'MCPToolRegistry', factory), patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(common), 2)  # Explicit API permission missing; no model call.
        self.assertTrue(created)
        self.assertTrue(all(client._process.poll() is not None for client in created[-1]._clients.values()))
        class Offline(ScriptedModel):
            api_key_env = 'SYNTHETIC_KEY'
        class InterruptedInput(io.StringIO):
            def readline(self, *args):
                raise KeyboardInterrupt()
        for stream in (io.StringIO('/exit\n'), InterruptedInput()):
            with patch.object(module, 'MCPToolRegistry', factory), patch('agent_lab.__main__.OpenAIModel', return_value=Offline([])), patch.dict(os.environ, {'SYNTHETIC_KEY': 'fake'}, clear=True), patch('sys.stdin', stream), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--db', str(self.home/'chat.sqlite'), 'chat', '--workspace', str(self.root),
                    '--model', 'fake', '--allow-api', '--mcp-config', str(self.config)]), 0)
            self.assertTrue(all(client._process.poll() is not None for client in created[-1]._clients.values()))


if __name__ == '__main__':
    unittest.main()
