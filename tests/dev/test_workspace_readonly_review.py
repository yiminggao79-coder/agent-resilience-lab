"""Independent whole-root contracts; synthetic workspaces, no native launch."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from agent_lab import sandbox
from agent_lab.coding_tools import CodingTools
from agent_lab.commands import validate_command
from agent_lab.permissions import ReadOnlyPolicy
from agent_lab.tools import ToolError, digest


class WorkspaceReadonlyReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base/'work'; self.root.mkdir()
        (self.root/'source').mkdir()
        (self.root/'source/code.txt').write_text('original\n')
        self.open = CodingTools(self.root)
        self.readonly = CodingTools(self.root, read_only_paths=['.'])

    def denied(self, operation):
        with self.assertRaises(ToolError) as error:
            operation()
        self.assertEqual(error.exception.code, 'read_only_path')

    def test_mutator_aliases_and_preexisting_intents_are_denied_during_recovery(self):
        calls = [
            ('write_file', {'path':'source/code.txt','content':'changed\n'}),
            ('append_file', {'path':'source/code.txt','content':'changed\n'}),
            ('apply_patch', {'path':'source/code.txt','old':'original','new':'changed'}),
            ('delete_file', {'path':'source/code.txt'}),
            ('create_directory', {'path':'future/nested','parents':True}),
            ('edit_file', {'path':'source/code.txt','expected_sha256':digest(b'original\n'),
                           'edits':[{'old':'original','new':'changed'}]}),
        ]
        patches = [
            '*** Add File: added.txt\n+added\n',
            '*** Delete File: source/code.txt\n',
            '*** Update File: source/code.txt\n@@\n-original\n+changed\n',
            '*** Update File: source/code.txt\n*** Move to: moved.txt\n@@\n-original\n+changed\n',
        ]
        calls += [('apply_patch_text', {'patch':'*** Begin Patch\n'+body+'*** End Patch\n'})
                  for body in patches]
        for name, args in calls:
            with self.subTest(tool=name, args=args):
                intent = self.open._prepare(name, args)
                self.denied(lambda: self.readonly._prepare(name, args))
                effects = intent['steps'] if name == 'apply_patch_text' else [intent]
                for effect in effects:
                    for method in (self.readonly._execute, self.readonly._recover):
                        self.denied(lambda: method(effect, 2))
        self.assertEqual((self.root/'source/code.txt').read_text(), 'original\n')
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*')),
                         ['source','source/code.txt'])

    def test_profiles_keep_read_grants_and_git_final_denies_after_substitution(self):
        scratch = self.base/'scratch'; scratch.mkdir()
        original = sandbox._profile(self.root, scratch)
        protected = sandbox._profile(self.root, scratch, read_only_paths=['.'])
        reads = lambda text: [line for line in text.splitlines() if line.startswith('(allow file-read')]
        self.assertEqual(reads(protected), reads(original))
        root = json.dumps(str(self.root)); temp = json.dumps(str(scratch))
        self.assertIn('(deny file-write* (literal %s) (subpath %s))' % (root, root), protected)
        for factory in (sandbox._profile, sandbox._command_profile):
            for overlap in (self.root, self.root/'scratch', self.base):
                with self.subTest(factory=factory.__name__, overlap=overlap):
                    with self.assertRaises(ToolError) as error:
                        factory(self.root, overlap, read_only_paths=['.'])
                    self.assertEqual(error.exception.code, 'read_only_scratch_overlap')
        # Git has its own unconditional read-only root/scratch policy. Its broad
        # grant substitution must not erase the final deny or leave a bare allow.
        git = sandbox._git_profile(self.root, scratch, Path(sys.executable).resolve())
        self.assertIn('(deny file-write* (subpath %s) (subpath %s))' % (root, temp), git)
        self.assertNotIn('(allow file-write*)', git)
        self.assertNotIn('(allow file-read*)', git)
        for line in git.splitlines():
            if line.startswith('(allow file-'):
                self.assertNotIn('(subpath %s)' % root, line)

    def test_old_scope_identity_and_read_admission_remain_separate_from_root_mode(self):
        legacy = ReadOnlyPolicy(self.root, ['source/code.txt','source'])
        frozen_manifest = b'{"read_only_paths":["source"],"version":1}'
        self.assertEqual(legacy.identity, hashlib.sha256(frozen_manifest).hexdigest())
        scoped = CodingTools(self.root, read_only_paths=['source'])
        command = scoped._prepare('run_command', {'argv':['true'],'cwd':'.'})
        self.assertEqual(validate_command(self.root, command, read_only_paths=['source']), command)
        with self.assertRaises(ToolError):
            validate_command(self.root, command, read_only_paths=['.'])
        read = self.readonly._prepare('read_file', {'path':'source/code.txt'})
        self.assertEqual(self.readonly._execute(read, 2)['content'], 'original\n')
        (self.base/'outside.txt').write_text('outside synthetic content')
        (self.root/'escape.txt').symlink_to(self.base/'outside.txt')
        (self.root/'.hidden').write_text('hidden synthetic content')
        (self.root/'linked.txt').write_text('linked synthetic content')
        os.link(self.root/'linked.txt', self.root/'linked-alias.txt')
        os.mkfifo(self.root/'fifo')
        for path in ('../outside.txt','escape.txt','.hidden','linked.txt','linked-alias.txt','fifo'):
            with self.subTest(path=path), self.assertRaises(ToolError):
                self.readonly._prepare('read_file', {'path':path})
