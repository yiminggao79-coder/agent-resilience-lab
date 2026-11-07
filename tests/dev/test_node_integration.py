"""Host configuration/authority contracts; scripted decisions, not LLM quality."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import Tools, ToolError, _worker_request
from tests.support import ScriptedModel, tool, final
from tests.dev.test_read_only_cli import Model


class NodeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'work'; self.root.mkdir()
        self.node = self.root.parent / 'host-node'
        self.node.write_bytes(b'synthetic executable identity only'); self.node.chmod(0o755)
        self.db = self.root.parent / 'state.sqlite'

    def tools(self):
        return CodingTools(self.root, node_executable=str(self.node))

    def test_worker_prepare_binds_host_runtime_even_for_python_and_default_is_legacy(self):
        tools = self.tools()
        intent = tools.prepare('run_command', {'argv': ['python', '-c', 'pass'], 'cwd': '.'})
        self.assertEqual(intent['command_runtime_policy'], tools.command_runtime_policy)
        node = tools.prepare('run_command', {'argv': ['node', '-e', '0'], 'cwd': '.'})
        self.assertEqual(node['argv'][0], str(self.node))
        self.assertNotIn('command_runtime_policy', CodingTools(self.root).prepare('run_command', {'argv': ['true'], 'cwd': '.'}))
        with self.assertRaises(ToolError):
            CodingTools(self.root).prepare('run_command', {'argv': ['node'], 'cwd': '.'})

    def test_node_only_os_reads_are_explicit_and_digest_bound(self):
        from agent_lab import sandbox
        tools = self.tools()
        names = ['hw.machine', 'hw.pagesize_compat', 'kern.hostname', 'kern.osrelease', 'kern.ostype', 'kern.version']
        self.assertEqual(tools.command_runtime_policy['sysctl_read'], names)
        profile = sandbox._command_profile(self.root, self.root.parent / 'scratch', node_executable=str(self.node))
        expected = '(allow sysctl-read ' + ' '.join('(sysctl-name ' + json.dumps(name) + ')' for name in names) + ')'
        self.assertIn(expected, profile)
        default = sandbox._command_profile(self.root, self.root.parent / 'scratch')
        self.assertNotIn('sysctl-read', default)
        self.assertNotIn('sysctl-name-prefix', profile)
        self.assertNotIn('(allow sysctl-read)', profile)
        self.assertNotIn('hw.memsize', profile)

    def test_enabled_schema_discloses_node_without_changing_default_schema_or_paths(self):
        baseline = CodingTools.schemas()
        self.assertEqual(CodingTools(self.root).schemas(), baseline)
        enabled = self.tools().schemas()
        description = next(item['function']['description'] for item in enabled if item['function']['name'] == 'run_command')
        self.assertIn('node:test', description)
        self.assertIn('--test-isolation=none', description)
        self.assertNotIn(str(self.node), json.dumps(enabled))
        self.assertNotEqual(enabled, baseline)

    def test_worker_rejects_tampered_runtime_envelope_before_preparing_tool(self):
        tools = self.tools()
        request = {'root': str(self.root), 'profile': 'coding', 'node_executable': str(self.node),
                   'command_runtime_policy': {'version': 1, 'node': {'sha256': '0' * 64}},
                   'operation': 'prepare', 'payload': {'name': 'run_command', 'args': {'argv': ['node'], 'cwd': '.'}}}
        output = io.StringIO()
        with patch('sys.stdin', io.StringIO(json.dumps(request))), contextlib.redirect_stdout(output), \
             patch.object(CodingTools, '_prepare', side_effect=AssertionError('must not prepare changed runtime')):
            _worker_request()
        self.assertEqual(json.loads(output.getvalue()), {'error': 'command_runtime_changed'})

    def test_runtime_admission_failure_confirms_durable_job_before_any_native_launch(self):
        from agent_lab import native_jobs
        from agent_lab.process_guard import rotate_generation
        tools = self.tools(); intent = tools._prepare('run_command', {'argv': ['node'], 'cwd': '.'})
        generation = rotate_generation(self.root)
        state = {'id': uuid.uuid4().hex, 'turn_id': uuid.uuid4().hex, 'workspace': str(self.root),
                 'phase': 'tool_pending', 'created_at': 1, 'messages': [], 'limits': {}, 'metrics': {},
                 'pending': {'status': 'running', 'operation_id': uuid.uuid4().hex, 'intent': intent}}
        with Store(self.db) as store: store.save(state, 'created')
        reference = native_jobs.claim_job(self.root, self.db, state['id'], state['turn_id'],
                                         state['pending']['operation_id'], generation, intent)
        request = {'root': str(self.root), 'profile': 'coding', 'node_executable': str(self.node),
                   'command_runtime_policy': tools.command_runtime_policy, 'native_job': reference,
                   'generation': generation, 'operation': 'execute', 'payload': intent, 'timeout': 3}
        self.node.unlink()  # Host change after claim, before worker admission.
        output = io.StringIO()
        with patch('sys.stdin', io.StringIO(json.dumps(request))), contextlib.redirect_stdout(output):
            _worker_request()
        self.assertEqual(json.loads(output.getvalue()), {'error': 'command_runtime_not_allowed'})
        native_jobs.require_quiescent(reference)
        native_jobs.ensure_workspace_idle(self.root)

    def test_checkpoint_drop_add_change_and_inplace_replacement_reject_before_model(self):
        with Store(self.db) as store:
            tools = self.tools(); model = ScriptedModel([final()]); engine = Engine(store, tools, model)
            tid = engine.create('Inspect project')
            state = store.load(tid)
            self.assertEqual(state['command_runtime_policy'], tools.command_runtime_policy)
            self.assertIn('node:test', state['messages'][0]['content'])
            self.assertNotIn(str(self.node), state['messages'][0]['content'])
            with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
                Engine(store, CodingTools(self.root), model).run(tid)
            self.node.write_bytes(b'changed host binary'); self.node.chmod(0o755)
            for changed in (tools, self.tools()):
                with self.subTest(cached=changed is tools), self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
                    Engine(store, changed, model).run(tid)
            legacy = Engine(store, CodingTools(self.root), ScriptedModel([final()]))
            old = legacy.create('Default')
            self.assertNotIn('command_runtime_policy', store.load(old))
            with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
                Engine(store, self.tools(), model).run(old)
            self.assertEqual(legacy.run(old)['phase'], 'completed')

    def test_approval_of_python_also_requires_matching_node_grant_and_abort_needs_none(self):
        with Store(self.db) as store:
            model = ScriptedModel([tool('run_command', {'argv': ['python', '-c', 'pass'], 'cwd': '.'})])
            engine = Engine(store, self.tools(), model); tid = engine.create('Run a command'); engine.run(tid)
            approval = engine.approval_request(tid)
            missing = Engine(store, Tools(self.root), None)
            with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
                missing.approval_request(tid)
            with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
                missing.decide_approval(tid, approval['request_id'], approval['digest'], True)
            self.assertEqual(store.load(tid)['phase'], 'awaiting_approval')
            self.assertEqual(missing.abort(tid)['phase'], 'failed')

    def test_fork_requires_runtime_and_preserves_policy_without_running(self):
        with Store(self.db) as store:
            engine = Engine(store, self.tools(), ScriptedModel([final()])); tid = engine.create('First'); engine.run(tid)
            with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
                Engine(store, CodingTools(self.root), None).fork_session(tid, 'Next')
            child = engine.fork_session(tid, 'Next')
            self.assertEqual(store.load(child)['command_runtime_policy'], store.load(tid)['command_runtime_policy'])
            self.assertEqual(store.load(child)['metrics']['model_calls'], 0)

    def test_continuation_and_compaction_reject_missing_runtime_before_sampling(self):
        with Store(self.db) as store:
            engine = Engine(store, self.tools(), ScriptedModel([final()]))
            tid = engine.create('First'); engine.run(tid)
            missing = Engine(store, CodingTools(self.root), ScriptedModel([]))
            for operation in (lambda: missing.continue_turn(tid, 'Second'), lambda: missing.compact(tid)):
                with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'): operation()
            before = store.load(tid)
            self.assertEqual(before['phase'], 'completed')
            self.assertEqual(before['metrics']['model_calls'], 1)

    def test_cli_run_resume_and_fork_forward_explicit_host_path(self):
        def call(args, model=None):
            out, err = io.StringIO(), io.StringIO()
            with patch('agent_lab.__main__.OpenAIModel', return_value=model or Model([])), \
                 patch.dict(os.environ, {'SYNTHETIC_KEY': 'synthetic-only'}), \
                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try: code = main(['--db', str(self.db), *args])
                except SystemExit as error: code = error.code
            return code, out.getvalue(), err.getvalue()
        common = ['--workspace', str(self.root), '--allow-api', '--model', 'synthetic']
        runtime = ['--node-executable', str(self.node)]
        code, out, err = call(['run', 'Inspect', *common, *runtime], Model([final()]))
        self.assertEqual(code, 0, err)
        with Store.open_readonly(self.db) as store: tid = store.db.execute('SELECT id FROM tasks').fetchone()[0]
        self.assertEqual(call(['resume', tid, *common])[0], 2)
        self.assertEqual(call(['resume', tid, *common, *runtime])[0], 0)
        code, out, err = call(['fork', tid, 'Second', '--workspace', str(self.root), *runtime])
        self.assertEqual(code, 0, err)
        with Store.open_readonly(self.db) as store:
            self.assertEqual(store.load(json.loads(out)['id'])['command_runtime_policy'], store.load(tid)['command_runtime_policy'])


if __name__ == '__main__': unittest.main()
