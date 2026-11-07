"""Backend authority contracts; all workspaces/envs are owned synthetic fixtures."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import commands
from agent_lab import sandbox, command_runtime
from agent_lab.command_runtime import runtime_policy
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import ToolError, _worker_operation
from tests.support import ScriptedModel, tool
try:
    from agent_lab.confined_backend import backend_descriptor, native_python_entry
except ImportError:
    backend_descriptor = native_python_entry = None


class ConfinedBackendTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(backend_descriptor, 'explicit backend authority not implemented')
        self.tmp = tempfile.TemporaryDirectory(prefix='agentlab-backend-', dir='/private/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.root = self.base/'work'; self.root.mkdir()

    def test_default_wire_unchanged_and_nondefault_binds_entry_and_cleanup(self):
        self.assertIsNone(runtime_policy(self.root))
        self.assertIsNone(runtime_policy(self.root, command_backend='nofork'))
        policy = runtime_policy(self.root, command_backend='confined-fork')
        backend = policy['backend']
        self.assertEqual(backend, backend_descriptor(self.root, 'confined-fork'))
        self.assertEqual(backend['entry']['path'], str(native_python_entry().resolve()))
        self.assertEqual(backend['deny_syscalls'], [66, 82, 147, 244])
        self.assertEqual(backend['cleanup_seconds'], 1.)
        self.assertEqual(backend['group_max_capacity'], 4096)
        for bad in ('shell', None, True, {'kind': 'confined-fork'}):
            with self.subTest(bad=bad), self.assertRaises(ToolError):
                runtime_policy(self.root, command_backend=bad)

    def test_all_commands_bind_backend_and_python_uses_native_entry(self):
        old = commands.prepare_command(self.root, ['echo','owned'])
        new = commands.prepare_command(self.root, ['echo','owned'], command_backend='confined-fork')
        self.assertNotEqual(old['command_digest'], new['command_digest'])
        self.assertNotEqual(old['sandbox_policy'], new['sandbox_policy'])
        for intent, backend in ((old, 'confined-fork'), (new, 'nofork')):
            with self.assertRaisesRegex(ToolError, '^command_changed$'):
                commands.validate_command(self.root, intent, command_backend=backend)
        python = commands.prepare_command(self.root, ['python','-c','pass'], command_backend='confined-fork')
        self.assertEqual(python['argv'][0], str(native_python_entry().resolve()))

    def test_backend_checkpoint_approval_and_fork_require_same_config(self):
        with Store(self.base/'private.sqlite') as store:
            tools = CodingTools(self.root, command_backend='confined-fork')
            engine = Engine(store, tools, ScriptedModel([tool('run_command', {'argv':['echo','owned'],'cwd':'.'}, 'one')]))
            task = engine.create('owned command')
            state = engine.run(task)
            self.assertEqual(state['phase'], 'awaiting_approval')
            request = engine.approval_request(task)
            self.assertIn('backend', store.load(task)['command_runtime_policy'])
            with self.assertRaises(ValueError):
                Engine(store, CodingTools(self.root), None).approval_request(task)
            self.assertEqual(engine.approval_request(task), request)

    def test_worker_forged_expected_policy_or_selector_fails_before_launch(self):
        policy = runtime_policy(self.root, command_backend='confined-fork')
        for backend, expected in (('nofork', policy), ('confined-fork', None)):
            request = {'root':str(self.root),'profile':'coding','operation':'prepare',
                       'payload':{'name':'run_command','args':{'argv':['echo','owned'],'cwd':'.'}},
                       'command_backend':backend,'command_runtime_policy':expected}
            with self.subTest(backend=backend), patch('agent_lab.sandbox.subprocess.Popen') as launch:
                with self.assertRaisesRegex(ToolError, '^command_runtime_changed$'):
                    _worker_operation(request, None)
                launch.assert_not_called()

    def test_run_tests_intent_binds_full_runtime_and_rejects_tampering(self):
        tools = CodingTools(self.root, command_backend='confined-fork')
        intent = tools.prepare('run_tests', {})
        self.assertEqual(intent['command_runtime_policy'], tools.command_runtime_policy)
        tampered = copy.deepcopy(intent); tampered.pop('command_runtime_policy')
        with patch('agent_lab.sandbox.run_tests') as launch:
            with self.assertRaisesRegex(ToolError, '^command_runtime_changed$'):
                tools._execute(tampered, 3)
            launch.assert_not_called()

    def test_native_entry_venv_is_admitted_wrapper_venv_is_not(self):
        environments = []
        for label, executable in (('native-env', native_python_entry()), ('wrapper-env', Path(sys.executable).resolve())):
            env = self.base/label
            created = subprocess.run([str(executable), '-I', '-m', 'venv', '--without-pip', str(env)],
                                     env={'PATH':os.defpath}, capture_output=True, timeout=5)
            self.assertEqual(created.returncode, 0, created.stderr)
            environments.append(env)
        good, bad = environments
        policy = runtime_policy(self.root, python_environment=good, command_backend='confined-fork')
        self.assertEqual(policy['python_environment']['base_executable'], policy['backend']['entry'])
        with self.assertRaises(ToolError):
            runtime_policy(self.root, python_environment=bad, command_backend='confined-fork')
        # Existing strict venv support is unchanged.
        self.assertIn('python_environment', runtime_policy(self.root, python_environment=bad))

    def test_schemas_report_actual_backend_without_model_selector_parameter(self):
        tools = CodingTools(self.root, command_backend='confined-fork')
        function = next(s['function'] for s in tools.schemas() if s['function']['name']=='run_command')
        self.assertEqual(set(function['parameters']['properties']), {'argv','cwd'})
        self.assertIn('confined-fork', function['description'])
        self.assertNotIn('pipelines/background children are unsupported', function['description'])

    def test_backend_change_is_detected_with_cached_tools_before_prepare(self):
        tools = CodingTools(self.root, command_backend='confined-fork')
        tools.command_backend = 'nofork'
        with self.assertRaisesRegex(ToolError, '^command_runtime_changed$'):
            tools.validate_command_runtime()

    def test_profile_construction_does_not_repeat_command_executable_hash(self):
        scratch=self.base/'scratch';scratch.mkdir()
        # A profile needs only the admitted path/resource rules. Commands are
        # already hashed before preparation and again after the policy probe.
        with patch.object(command_runtime,'executable_descriptor',wraps=command_runtime.executable_descriptor) as hashed:
            sandbox._command_profile(self.root,scratch,node_executable=str(Path(sys.executable).resolve()))
        self.assertEqual(hashed.call_count,0,'profile construction rehash consumes the command execution deadline')


if __name__ == '__main__':unittest.main()
