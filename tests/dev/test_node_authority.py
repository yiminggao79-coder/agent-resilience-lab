"""Host-only runtime authority over synthetic executable files; no Node launch."""
import copy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import commands
from agent_lab.tools import ToolError


class NodeAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('agent_lab.command_runtime'),
                             'explicit command runtime authority is missing')
        self.runtime = importlib.import_module('agent_lab.command_runtime')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'project'; self.root.mkdir()
        self.node = self.root.parent / 'synthetic-runtime'; self.node.write_bytes(b'synthetic executable bytes')
        self.node.chmod(0o755)

    def prepare(self, argv=None, **kwargs):
        return commands.prepare_command(self.root, argv or ['node', 'script.js'], node_executable=self.node, **kwargs)

    def test_default_authority_and_aliases_are_unchanged_and_never_infer_environment(self):
        with patch.dict(os.environ, {'PATH': str(self.node.parent), 'NODE_OPTIONS': '--untrusted', 'NODE_PATH': 'PRIVATE'}):
            self.assertIsNone(self.runtime.runtime_policy(self.root))
            self.assertNotIn('node', commands.trusted_executables())
            default = commands.prepare_command(self.root, ['python', '-c', 'pass'])
        self.assertNotIn('command_runtime_policy', default)
        self.assertEqual(default, commands.prepare_command(self.root, ['python', '-c', 'pass'], node_executable=None))
        with self.assertRaisesRegex(ToolError, '^command_not_allowed$'):
            commands.prepare_command(self.root, ['node', 'script.js'])
        self.assertNotIn('NODE_', json.dumps(default))

    def test_streaming_descriptor_is_stable_bounded_and_has_only_existing_fields(self):
        data = b'0123456789abcdef' * 140000
        self.node.write_bytes(data)
        reads, actual = [], os.read
        def read(fd, count): reads.append(count); return actual(fd, count)
        with patch.object(self.runtime.os, 'read', side_effect=read), patch.object(Path, 'read_bytes', side_effect=AssertionError('unbounded executable read')):
            policy = self.runtime.runtime_policy(self.root, self.node)
        self.assertEqual(set(policy), {'version', 'node', 'sysctl_read'})
        self.assertEqual(policy['sysctl_read'], ['hw.machine', 'hw.pagesize_compat', 'kern.hostname', 'kern.osrelease', 'kern.ostype', 'kern.version'])
        self.assertEqual(policy['version'], 1)
        descriptor = policy['node']; info = self.node.stat()
        self.assertEqual(descriptor, {'path': str(self.node), 'device': info.st_dev, 'inode': info.st_ino,
                                      'size': len(data), 'mtime_ns': info.st_mtime_ns,
                                      'sha256': hashlib.sha256(data).hexdigest()})
        self.assertGreater(len(reads), 2)
        self.assertLessEqual(max(reads), 1024 * 1024)
        self.assertEqual(policy, self.runtime.runtime_policy(self.root, self.node))

    def test_explicit_alias_and_host_launcher_symlink_bind_canonical_runtime(self):
        launcher = self.node.parent / 'host-launcher'; launcher.symlink_to(self.node)
        self.assertEqual(commands.trusted_executables(node_executable=launcher)['node'], str(self.node))
        intent = commands.prepare_command(self.root, ['node', '--test', '--test-isolation=none', 'test.js'], node_executable=launcher)
        self.assertEqual(intent['argv'][0], str(self.node))
        self.assertEqual(intent['requested_argv'][0], 'node')
        self.assertEqual(intent['executable'], intent['command_runtime_policy']['node'])
        self.assertEqual(commands.validate_command(self.root, intent, node_executable=launcher), intent)
        self.assertEqual(self.runtime.runtime_policy(self.root, launcher), self.runtime.runtime_policy(self.root, self.node))

    def test_whole_optional_grant_binds_python_commands_and_changed_grant_cannot_reuse_approval(self):
        argv = ['python', '-c', 'pass']
        enabled = self.prepare(argv)
        default = commands.prepare_command(self.root, argv)
        self.assertNotEqual(enabled['command_digest'], default['command_digest'])
        self.assertEqual(enabled['command_runtime_policy'], self.runtime.runtime_policy(self.root, self.node))
        with self.assertRaises(ToolError): commands.validate_command(self.root, enabled)
        with self.assertRaises(ToolError): commands.validate_command(self.root, default, node_executable=self.node)
        forged = copy.deepcopy(enabled); forged['command_runtime_policy']['node']['sha256'] = '0' * 64
        with self.assertRaises(ToolError): commands.validate_command(self.root, forged, node_executable=self.node)
        self.node.write_bytes(b'changed executable identity')
        with self.assertRaises(ToolError): commands.validate_command(self.root, enabled, node_executable=self.node)

    def test_workspace_paths_and_both_directions_of_launcher_alias_are_rejected(self):
        inside = self.root / 'node'; inside.write_bytes(b'workspace executable'); inside.chmod(0o755)
        host_alias = self.root.parent / 'outside-alias'; host_alias.symlink_to(inside)
        project_alias = self.root / 'launcher'; project_alias.symlink_to(self.node)
        for node in ('relative-node', inside, host_alias, project_alias, self.root / '..' / self.node.name, 3, ''):
            with self.subTest(node=node), self.assertRaises(ToolError):
                self.runtime.runtime_policy(self.root, node)

    def test_hardlink_alias_into_workspace_is_not_an_admitted_runtime(self):
        os.link(self.node, self.root / '.hidden-runtime-alias')
        with self.assertRaises(ToolError): self.runtime.runtime_policy(self.root, self.node)

    def test_nonregular_nonexecutable_and_oversize_are_rejected_before_open(self):
        fifo = self.root.parent / 'fifo'; os.mkfifo(fifo, 0o755)
        directory = self.root.parent / 'directory'; directory.mkdir()
        noexec = self.root.parent / 'noexec'; noexec.write_bytes(b'not executable'); noexec.chmod(0o600)
        huge = self.root.parent / 'huge';
        with huge.open('wb') as file: file.truncate(256 * 1024 * 1024 + 1)
        huge.chmod(0o755)
        for node in (fifo, directory, noexec, huge):
            with self.subTest(node=node), patch.object(self.runtime.os, 'open', side_effect=AssertionError('unsafe executable opened')):
                with self.assertRaises(ToolError): self.runtime.runtime_policy(self.root, node)

    def test_change_during_stream_hash_fails_closed(self):
        self.node.write_bytes(b'x' * (2 * 1024 * 1024))
        actual = os.read; changed = []
        def read(fd, count):
            data = actual(fd, count)
            if not changed:
                changed.append(True); self.node.write_bytes(b'changed during hash')
            return data
        with patch.object(self.runtime.os, 'read', side_effect=read):
            with self.assertRaises(ToolError): self.runtime.runtime_policy(self.root, self.node)

    def test_default_selected_binary_also_uses_bounded_descriptor_without_shape_change(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('unbounded legacy executable read')):
            intent = commands.prepare_command(self.root, ['python', '-c', 'pass'])
        path = Path(sys.executable).resolve(); info = path.stat()
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for data in iter(lambda: source.read(1024 * 1024), b''): digest.update(data)
        self.assertEqual(intent['executable'], {'path': str(path), 'device': info.st_dev, 'inode': info.st_ino,
                                               'size': info.st_size, 'mtime_ns': info.st_mtime_ns, 'sha256': digest.hexdigest()})


if __name__ == '__main__': unittest.main()
