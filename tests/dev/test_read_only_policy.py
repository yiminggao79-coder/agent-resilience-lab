"""Host protection tests use fresh synthetic workspaces only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import ToolError
from tests.support import ScriptedModel, final


class ReadOnlyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        (self.root / 'snapshot' / 'data').mkdir(parents=True)
        (self.root / 'snapshot' / 'data' / 'source.txt').write_text('original\n')
        (self.root / 'edit.txt').write_text('editable\n')

    def protected(self, paths=('snapshot/data',)):
        return CodingTools(self.root, read_only_paths=paths)

    def denied(self, action):
        with self.assertRaises(ToolError) as raised:
            action()
        self.assertEqual(raised.exception.code, 'read_only_path')

    def test_bounded_normalized_configuration_and_stable_identity(self):
        policy = self.protected(['snapshot/data', 'snapshot', 'snapshot/data']).read_only_policy
        self.assertEqual(policy.paths, ('snapshot',))
        self.assertEqual(policy.manifest, {'version': 1, 'read_only_paths': ['snapshot']})
        self.assertEqual(policy.identity, self.protected(['snapshot']).read_only_policy.identity)
        for invalid in ([''], ['../x'], ['/tmp/x'], ['snapshot/'], ['snapshot//data'],
                        ['snapshot/./data'], ['.hidden'], ['snapshot/../edit.txt'], ['a\\b'],
                        ['a' * 513], ['a/' * 32 + 'a'], ['s' + str(n) for n in range(33)], 'snapshot', None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.protected(invalid)

    def test_prepare_denies_mutations_and_allows_protected_reads(self):
        tools = self.protected()
        calls = [('write_file', {'path': 'snapshot/data/source.txt', 'content': 'changed'}),
                 ('append_file', {'path': 'snapshot/data/source.txt', 'content': 'changed'}),
                 ('apply_patch', {'path': 'snapshot/data/source.txt', 'old': 'original', 'new': 'changed'}),
                 ('delete_file', {'path': 'snapshot/data/source.txt'}),
                 ('create_directory', {'path': 'snapshot/data/new', 'parents': False}),
                 ('write_file', {'path': 'snapshot', 'content': 'ancestor'})]
        for name, args in calls:
            with self.subTest(name=name):
                self.denied(lambda: tools.prepare(name, args))
        intent = tools.prepare('read_file', {'path': 'snapshot/data/source.txt'})
        self.assertEqual(tools.execute(intent, 2)['content'], 'original\n')
        write = tools.prepare('write_file', {'path': 'edit.txt', 'content': 'updated'})
        self.assertTrue(tools.execute(write, 2)['ok'])
        self.assertEqual((self.root / 'edit.txt').read_text(), 'updated')

    def test_execution_and_recovery_recheck_prepared_unprotected_intents(self):
        base = CodingTools(self.root)
        for name, args in [('write_file', {'path': 'snapshot/data/source.txt', 'content': 'changed'}),
                           ('append_file', {'path': 'snapshot/data/source.txt', 'content': 'changed'}),
                           ('delete_file', {'path': 'snapshot/data/source.txt'}),
                           ('create_directory', {'path': 'snapshot/data/new', 'parents': True})]:
            intent = base.prepare(name, args)
            for operation in ('execute', 'recover'):
                with self.subTest(name=name, operation=operation):
                    self.denied(lambda: getattr(self.protected(), operation)(intent, 2))
        self.assertEqual((self.root / 'snapshot/data/source.txt').read_text(), 'original\n')

    def test_future_scope_and_case_aliases_cannot_bypass_protection(self):
        tools = self.protected(['snapshot/data/future'])
        self.denied(lambda: tools.prepare('write_file', {'path': 'snapshot/data/future', 'content': 'x'}))
        self.denied(lambda: self.protected().prepare('write_file', {'path': 'Snapshot/DATA/source.txt', 'content': 'x'}))

    def test_protected_links_are_refused_at_configuration_and_execution(self):
        target = self.root / 'snapshot/data/source.txt'
        os.link(target, self.root / 'alias.txt')
        with self.assertRaises(ValueError):
            self.protected()
        (self.root / 'alias.txt').unlink()
        tools = self.protected()
        target.unlink()
        target.symlink_to(self.root / 'edit.txt')
        with self.assertRaises(ToolError):
            tools.prepare('read_file', {'path': 'edit.txt'})
        with self.assertRaises(ValueError):
            self.protected()

    def test_checkpoint_binds_policy_and_legacy_empty_remains_compatible(self):
        store = Store(Path(self.tmp.name) / 'private' / 'state.sqlite')
        self.addCleanup(store.close)
        model = ScriptedModel([final()])
        engine = Engine(store, self.protected(), model)
        task_id = engine.create('Inspect and edit allowed data')
        state = store.load(task_id)
        self.assertEqual(state['read_only_policy'], self.protected().read_only_policy.manifest)
        self.assertEqual(state['read_only_policy_identity'], self.protected().read_only_policy.identity)
        for tools in (CodingTools(self.root), self.protected(['snapshot'])):
            with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
                Engine(store, tools, model).run(task_id)
        self.assertEqual(engine.run(task_id)['phase'], 'completed')
        old = Engine(store, CodingTools(self.root), ScriptedModel([final()]))
        legacy_id = old.create('Legacy behavior')
        self.assertNotIn('read_only_policy', store.load(legacy_id))
        self.assertEqual(old.run(legacy_id)['phase'], 'completed')

    def test_mcp_refuses_before_reading_config_or_starting_external_process(self):
        from agent_lab.registry import MCPToolRegistry
        with self.assertRaisesRegex(ValueError, 'read_only_policy_mcp_unsupported'):
            MCPToolRegistry(self.protected(), '/does/not/exist')

    def test_command_authority_binds_policy_and_checks_host_policy(self):
        from agent_lab.commands import validate_command
        tools = self.protected()
        intent = tools.prepare('run_command', {'argv': ['true'], 'cwd': '.'})
        self.assertEqual(intent['read_only_policy'], tools.read_only_policy.manifest)
        self.assertEqual(intent['read_only_policy_identity'], tools.read_only_policy.identity)
        self.assertEqual(validate_command(self.root, intent, read_only_paths=tools.read_only_policy.paths), intent)
        unprotected = CodingTools(self.root).prepare('run_command', {'argv': ['true'], 'cwd': '.'})
        with self.assertRaises(ToolError):
            validate_command(self.root, unprotected, read_only_paths=tools.read_only_policy.paths)
        with self.assertRaises(ToolError):
            validate_command(self.root, intent)
        self.assertNotEqual(unprotected['command_digest'], intent['command_digest'])

    def test_unavailable_backend_never_executes_protected_commands_or_tests(self):
        from agent_lab import sandbox
        tools = self.protected()
        with patch.object(sandbox, 'BACKEND_PATH', '/nonexistent/sandbox-exec'):
            for name, args in [('run_command', {'argv': ['python', '-c', "open('executed','w').write('x')"], 'cwd': '.'}),
                               ('run_tests', {})]:
                intent = tools._prepare(name, args)
                with self.assertRaisesRegex(ToolError, 'sandbox_unavailable'):
                    tools._execute(intent, 3)
        self.assertFalse((self.root / 'executed').exists())

    def native_available(self):
        if not Path('/usr/bin/sandbox-exec').is_file():
            self.skipTest('macOS sandbox unavailable; no bare fallback')
        result = subprocess.run(['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)', '/usr/bin/true'],
                                capture_output=True, timeout=2)
        if result.returncode:
            self.skipTest('host denies nested sandbox-exec; no bare fallback')

    def native_body(self):
        return '''import os
from pathlib import Path
source = Path('snapshot/data/source.txt')
assert source.read_text() == 'original\\n'
def denied(operation):
    try:
        operation()
    except PermissionError:
        pass
    else:
        raise AssertionError('protected operation succeeded')
denied(lambda: source.write_text('changed'))
denied(lambda: source.unlink())
denied(lambda: source.rename('escaped.txt'))
denied(lambda: Path('edit.txt').replace(source))
denied(lambda: Path('snapshot/data').rename('escaped-data'))
denied(lambda: Path('snapshot').rename('escaped-parent'))
denied(lambda: Path('snapshot/data/new.txt').write_text('changed'))
denied(lambda: os.link(source, 'hardlink.txt'))
assert source.read_text() == 'original\\n'
Path('allowed.txt').write_text('allowed')
Path('snapshot/allowed.txt').write_text('allowed sibling')
if Path('SNAPSHOT/DATA/source.txt').exists():
    denied(lambda: Path('SNAPSHOT/DATA/source.txt').write_text('alias mutation'))
print('protection verified')
'''

    def test_native_managed_command_denies_protected_operations_and_allows_reads_writes(self):
        self.native_available()
        tools = self.protected()
        intent = tools.prepare('run_command', {'argv': ['python', '-c', self.native_body()], 'cwd': '.'})
        result = tools.execute(intent, 8)
        self.assertEqual(result['exit_code'], 0, result)
        self.assertIn('protection verified', result['stdout'])
        self.assertEqual((self.root / 'allowed.txt').read_text(), 'allowed')
        self.assertEqual((self.root / 'snapshot/data/source.txt').read_text(), 'original\n')

    def test_native_run_tests_and_supervisor_preserve_protection(self):
        self.native_available()
        (self.root / 'tests').mkdir()
        body = self.native_body()
        (self.root / 'tests/test_probe.py').write_text('import unittest\nclass Probe(unittest.TestCase):\n def test_policy(self):\n' +
                                                       ''.join('  ' + line + '\n' for line in body.splitlines()))
        tools = self.protected()
        result = tools.execute(tools.prepare('run_tests', {}), 8)
        self.assertEqual(result['exit_code'], 0, result)
        self.assertIn('protection verified', result['stdout'])
        self.assertEqual((self.root / 'allowed.txt').read_text(), 'allowed')

    def test_sandbox_policy_contains_exact_subtree_and_ancestor_rules(self):
        from agent_lab import sandbox
        policy = sandbox._profile(self.root.resolve(), Path(self.tmp.name).resolve() / 'scratch',
                                  read_only_paths=('snapshot/data',))
        for operation in ('file-write*', 'file-write-unlink', 'file-link'):
            self.assertIn('(deny ' + operation, policy)
        self.assertIn('(literal ' + json.dumps(str(self.root.resolve() / 'snapshot/data')) + ')', policy)
        self.assertIn('(subpath ' + json.dumps(str(self.root.resolve() / 'snapshot/data')) + ')', policy)
        self.assertIn('(literal ' + json.dumps(str(self.root.resolve() / 'snapshot')) + ')', policy)

    def test_native_missing_ancestor_cannot_be_created_as_symlink_escape(self):
        self.native_available()
        tools = self.protected(['future-parent/nested/locked.txt'])
        body = '''from pathlib import Path
try:
    Path('future-parent').symlink_to(Path.cwd() / 'snapshot', target_is_directory=True)
except PermissionError:
    pass
else:
    Path('future-parent/nested').mkdir()
    Path('future-parent/nested/locked.txt').write_text('escaped')
    raise AssertionError('missing ancestor symlink escaped protection')
Path('allowed.txt').write_text('allowed')
'''
        result = tools.execute(tools.prepare('run_command', {'argv': ['python', '-c', body], 'cwd': '.'}), 8)
        self.assertEqual(result['exit_code'], 0, result)
        self.assertFalse((self.root / 'future-parent').is_symlink())
        self.assertFalse((self.root / 'snapshot/nested/locked.txt').exists())

    def test_approval_policy_check_refuses_missing_or_changed_host_scopes(self):
        from tests.support import tool
        store = Store(Path(self.tmp.name) / 'private' / 'approval.sqlite')
        self.addCleanup(store.close)
        model = ScriptedModel([tool('run_command', {'argv': ['true'], 'cwd': '.'})])
        engine = Engine(store, self.protected(), model)
        task_id = engine.create('Run true')
        engine.run(task_id)
        approval = engine.approval_request(task_id)
        bare = Engine(store, CodingTools(self.root), model)
        with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
            bare.approval_request(task_id)
        with self.assertRaisesRegex(ValueError, 'resume_configuration_mismatch'):
            bare.decide_approval(task_id, approval['request_id'], approval['digest'], True)
        self.assertEqual(store.load(task_id)['phase'], 'awaiting_approval')

    def test_native_read_only_descriptor_mutation_fcntls_are_denied(self):
        self.native_available()
        tools = self.protected()
        body = '''import fcntl, os
fd = os.open('snapshot/data/source.txt', os.O_RDONLY)
for operation in (80, 110):
    try:
        fcntl.fcntl(fd, operation, 0)
    except PermissionError:
        pass
    else:
        raise AssertionError('descriptor mutation was not denied')
os.close(fd)
'''
        result = tools.execute(tools.prepare('run_command', {'argv': ['python', '-c', body], 'cwd': '.'}), 8)
        self.assertEqual(result['exit_code'], 0, result)
        self.assertEqual((self.root / 'snapshot/data/source.txt').read_text(), 'original\n')

    def test_native_unicode_normalization_alias_existing_and_future_are_refused(self):
        self.native_available()
        for present in (True, False):
            with self.subTest(existing=present):
                target = self.root / '\u00e9.txt'
                alias = self.root / 'e\u0301.txt'
                target.unlink(missing_ok=True)
                if present:
                    target.write_text('original')
                    self.assertTrue(target.samefile(alias))
                tools = self.protected(['\u00e9.txt'])
                body = "from pathlib import Path; Path('e\\u0301.txt').write_text('escaped')"
                try:
                    intent = tools.prepare('run_command', {'argv': ['python', '-c', body], 'cwd': '.'})
                    tools.execute(intent, 8)
                except ToolError as error:
                    self.assertEqual(error.code, 'read_only_native_path_unsupported')
                else:
                    content = target.read_text() if target.exists() else None
                    samefile = target.exists() and target.samefile(alias)
                    self.fail('native Unicode alias launched: existing=%s content=%r samefile=%s' %
                              (present, content, samefile))
                if present:
                    self.assertEqual(target.read_text(), 'original')
                    target.unlink()
                else:
                    self.assertFalse(target.exists())

    def test_non_ascii_scope_refused_at_prepare_execute_recover_and_profile(self):
        from agent_lab import sandbox
        target = self.root / '\u00e9.txt'
        target.write_text('original')
        tools = self.protected(['\u00e9.txt'])
        command = CodingTools(self.root).prepare('run_command', {'argv': ['true'], 'cwd': '.'})
        for name, args, intent in [('run_command', {'argv': ['true'], 'cwd': '.'}, command),
                                  ('run_tests', {}, {'name': 'run_tests', 'args': {}, 'policy': 'unsafe'})]:
            for operation in ('prepare', 'execute', 'recover'):
                with self.subTest(name=name, operation=operation):
                    with self.assertRaisesRegex(ToolError, 'read_only_native_path_unsupported'):
                        if operation == 'prepare':
                            tools.prepare(name, args)
                        else:
                            getattr(tools, operation)(intent, 3)
        with self.assertRaisesRegex(ToolError, 'read_only_native_path_unsupported'):
            sandbox._profile(self.root.resolve(), Path(self.tmp.name).resolve() / 'scratch', read_only_paths=tools.read_only_policy.paths)
        read = tools.prepare('read_file', {'path': '\u00e9.txt'})
        self.assertEqual(tools.execute(read, 2)['content'], 'original')
        self.denied(lambda: tools.prepare('write_file', {'path': 'e\u0301.txt', 'content': 'changed'}))
        allowed = tools.prepare('write_file', {'path': 'edit.txt', 'content': 'allowed'})
        self.assertTrue(tools.execute(allowed, 2)['ok'])

    def test_non_ascii_absolute_workspace_root_refuses_native_ascii_scope(self):
        from agent_lab import sandbox
        root = self.root / '\u00e9-project'
        root.mkdir()
        (root / 'locked.txt').write_text('original')
        tools = CodingTools(root, read_only_paths=['locked.txt'])
        for name, args in [('run_command', {'argv': ['true'], 'cwd': '.'}), ('run_tests', {})]:
            with self.subTest(name=name), self.assertRaisesRegex(ToolError, 'read_only_native_path_unsupported'):
                tools.prepare(name, args)
        with self.assertRaisesRegex(ToolError, 'read_only_native_path_unsupported'):
            sandbox._profile(root.resolve(), Path(self.tmp.name).resolve() / 'scratch', read_only_paths=('locked.txt',))
