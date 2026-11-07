"""Actual local Git repositories and Seatbelt; no API calls or project commits."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.tools import ToolError
from agent_lab import sandbox
try:
    from agent_lab import git_tools
except ImportError:
    git_tools = None

GIT = '/Library/Developer/CommandLineTools/usr/bin/git'


class GitToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'project'
        self.root.mkdir()
        if not Path(GIT).is_file():
            self.skipTest('fixed developer Git unavailable')
        self.git('init', '-q')
        (self.root/'calc.py').write_text('def add(a,b):\n    return a-b\n')
        (self.root/'.env').write_text('PRIVATE_TRACKED_SOURCE=initial\n')
        self.git('add', '--', 'calc.py', '.env')
        self.tools = CodingTools(self.root)

    def git(self, *args):
        env = {'PATH': os.defpath, 'LANG': 'C', 'HOME': str(self.root),
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
        result = subprocess.run([GIT, *args], cwd=self.root, env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def call(self, name, args):
        intent = self.tools.prepare(name, args, timeout=5)
        self.assertEqual(intent['policy'], 'read')
        return self.tools.execute(intent, 8)

    def test_registered_status_and_real_diff_after_write(self):
        names = {schema['function']['name'] for schema in self.tools.schemas()}
        self.assertTrue({'git_status', 'git_diff'} <= names, 'fixed Git tools are not registered')
        (self.root/'calc.py').write_text('def add(a,b):\n    return a+b\n')
        (self.root/'new.txt').write_text('untracked data')
        (self.root/'.env').write_text('PRIVATE_TRACKED_SOURCE=changed\n')
        status = self.call('git_status', {})
        self.assertTrue(status['ok'])
        self.assertEqual(status['exit_code'], 0)
        entries = {entry['path']: entry for entry in status['entries']}
        self.assertEqual(entries['calc.py']['worktree'], 'M')
        self.assertEqual(entries['new.txt']['index'], '?')
        self.assertNotIn('.env', entries)
        diff = self.call('git_diff', {'path': 'calc.py'})
        self.assertTrue(diff['ok'])
        self.assertIn('-    return a-b', diff['diff'])
        self.assertIn('+    return a+b', diff['diff'])
        self.assertNotIn('PRIVATE_TRACKED_SOURCE', json.dumps(diff))
        self.assertEqual(self.call('git_diff', {'path': 'new.txt'})['diff'], '')

    def test_status_filters_type_changed_links_without_hiding_visible_entries(self):
        (self.root/'target').write_text('visible data')
        (self.root/'calc.py').unlink()
        (self.root/'calc.py').symlink_to(self.root/'target')
        status = self.call('git_status', {})
        self.assertTrue(status['ok'])
        entries = {entry['path']: entry for entry in status['entries']}
        self.assertNotIn('calc.py', entries)
        self.assertEqual(entries['target']['index'], '?')

    def test_status_never_updates_git_metadata(self):
        def snapshot():
            return {str(p.relative_to(self.root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                    for p in (self.root/'.git').rglob('*') if p.is_file()}
        before = snapshot()
        (self.root/'calc.py').write_text('changed\n')
        self.call('git_status', {})
        self.call('git_diff', {'path': 'calc.py'})
        self.assertEqual(snapshot(), before)

    def test_helpers_and_ambient_configuration_are_not_executed(self):
        trap = self.root/'trap.sh'
        marker = self.root/'executed'
        trap.write_text('#!/bin/sh\nprintf bad > ' + str(marker) + '\n')
        trap.chmod(0o755)
        for key in ('core.fsmonitor', 'diff.external', 'diff.hostile.textconv', 'core.pager'):
            self.git('config', key, str(trap))
        self.git('config', 'core.hooksPath', str(self.root/'hooks'))
        (self.root/'hooks').mkdir()
        (self.root/'hooks'/'post-index-change').write_text(trap.read_text())
        (self.root/'hooks'/'post-index-change').chmod(0o755)
        (self.root/'.git'/'info'/'attributes').write_text('calc.py diff=hostile\n')
        (self.root/'calc.py').write_text('changed\n')
        outside = Path(self.tmp.name)/'outside'
        outside.mkdir()
        with patch.dict(os.environ, {'GIT_EXTERNAL_DIFF': str(trap), 'GIT_DIR': str(outside),
                                     'GIT_CONFIG_GLOBAL': str(trap), 'PRIVATE_FAKE_KEY': 'SENTINEL'}):
            self.assertTrue(self.call('git_status', {})['ok'])
            self.assertTrue(self.call('git_diff', {'path': 'calc.py'})['ok'])
        self.assertFalse(marker.exists())

    def test_hidden_traversal_links_and_magic_pathspec_cannot_expose_source(self):
        (self.root/'.env').write_text('PRIVATE_TRACKED_SOURCE=changed\n')
        outside = Path(self.tmp.name)/'private'
        outside.write_text('OUTSIDE_DATA')
        (self.root/'link').symlink_to(outside)
        for path in ('.env', '.git/config', '../private', str(outside), 'link', 'folder/../calc.py'):
            with self.subTest(path=path), self.assertRaises(ToolError):
                self.tools.prepare('git_diff', {'path': path})
        result = self.call('git_diff', {'path': ':(glob)**'})
        self.assertNotIn('PRIVATE_TRACKED_SOURCE', json.dumps(result))
        self.assertEqual(result['diff'], '')

    def test_repository_must_be_local_real_directory_without_redirects(self):
        self.assertIsNotNone(git_tools, 'fixed Git backend missing')
        original = self.root/'.git'
        moved = Path(self.tmp.name)/'metadata'
        original.rename(moved)
        original.write_text('gitdir: ' + str(moved) + '\n')
        with self.assertRaises(ToolError):
            git_tools.prepare_git(self.root, 'git_status', {})
        original.unlink()
        original.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ToolError):
            git_tools.prepare_git(self.root, 'git_status', {})
        original.unlink()
        moved.rename(original)
        (original/'objects'/'info'/'alternates').write_text(str(Path(self.tmp.name))+'\n')
        with self.assertRaises(ToolError):
            git_tools.prepare_git(self.root, 'git_status', {})
        nested = self.root/'nested'
        nested.mkdir()
        with self.assertRaises(ToolError):
            git_tools.prepare_git(nested, 'git_status', {})

    def test_git_profile_denies_writes_hidden_reads_network_and_fork(self):
        self.assertIsNotNone(git_tools, 'fixed Git backend missing')
        hidden = ['.env', '.a', '.g', '.gi', '.gix', '.gitx', '.github', '.gitignore', '.git-config']
        for name in hidden[1:]:
            (self.root/name).write_text('HIDDEN_SOURCE')
        (self.root/'visible').mkdir()
        (self.root/'visible'/'.nested').write_text('HIDDEN_SOURCE')
        hidden.append('visible/.nested')
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp).resolve()
            profile = sandbox._git_profile(self.root.resolve(), scratch, git_tools.trusted_git())
            code = ('import pathlib,socket,os\n'
                    'assert pathlib.Path("calc.py").read_text()\n'
                    'assert pathlib.Path(".git/HEAD").read_text()\n'
                    'for p in ("calc.py", ".git/index", "new-output"):\n'
                    ' try: pathlib.Path(p).write_text("bad")\n'
                    ' except OSError: pass\n'
                    ' else: raise AssertionError("write permitted")\n'
                    'for p in %r:\n'
                    ' try: pathlib.Path(p).read_text()\n'
                    ' except OSError: pass\n'
                    ' else: raise AssertionError("hidden read permitted: "+p)\n'
                    'try: os.fork()\n'
                    'except OSError: pass\n'
                    'else: raise AssertionError("fork permitted")\n'
                    'try: socket.socket().connect(("127.0.0.1",9))\n'
                    'except OSError: pass\n'
                    'else: raise AssertionError("network permitted")\n') % hidden
            result = sandbox._collect([sandbox.BACKEND_PATH, '-p', profile, sys.executable, '-I', '-S', '-c', code],
                                      self.root, {'PATH': os.defpath, 'LANG': 'C'}, 5)
        self.assertEqual(result['exit_code'], 0, result['stderr'])

    def test_nonzero_failure_and_missing_backend_are_explicit(self):
        self.assertIsNotNone(git_tools, 'fixed Git backend missing')
        (self.root/'.git'/'index').write_bytes(b'broken index')
        result = self.call('git_status', {})
        self.assertFalse(result['ok'])
        self.assertNotEqual(result['exit_code'], 0)
        self.assertEqual(result['error'], 'git_failed')
        with patch.object(git_tools, 'GIT_BINARY', '/nonexistent/git'):
            with self.assertRaises(ToolError) as error:
                git_tools.execute_git(self.root, {'name': 'git_status', 'args': {}, 'policy': 'read'}, 2)
        self.assertEqual(error.exception.code, 'git_unavailable')

    def test_diff_and_status_output_are_bounded(self):
        (self.root/'calc.py').write_text('many\\n' * 100000)
        diff = self.call('git_diff', {'path': 'calc.py'})
        self.assertTrue(diff['truncated'])
        self.assertLessEqual(len(json.dumps(diff, ensure_ascii=False).encode()), 65536)
        for i in range(220):
            (self.root/('new_%03d' % i)).write_text('x')
        status = self.call('git_status', {})
        self.assertTrue(status['truncated'])
        self.assertLessEqual(len(status['entries']), 200)
        self.assertLessEqual(len(json.dumps(status, ensure_ascii=False).encode()), 65536)


if __name__ == '__main__':
    unittest.main()
