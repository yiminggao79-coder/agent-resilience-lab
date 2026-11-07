"""Exact host root-readonly sentinel, synthetic trees and scratch scopes only."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unicodedata
import unittest
from unittest.mock import patch

from agent_lab.permissions import ReadOnlyPolicy


class WorkspaceReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'Workspace'; self.root.mkdir()
        (self.root / 'source').mkdir()
        (self.root / 'source' / 'code.py').write_text('original')

    def whole(self, paths=('.',)): return ReadOnlyPolicy(self.root, paths)

    def test_exact_root_sentinel_dominates_valid_descendants_with_version_one_identity(self):
        policy = self.whole(['source/code.py', '.', 'source', '.'])
        self.assertTrue(policy.whole_workspace)
        self.assertEqual(policy.paths, ('.',))
        self.assertEqual(policy.manifest, {'version': 1, 'read_only_paths': ['.']})
        encoded = json.dumps(policy.manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        self.assertEqual(policy.identity, hashlib.sha256(encoded).hexdigest())
        self.assertEqual(policy.identity, self.whole().identity)
        self.assertNotEqual(policy.identity, ReadOnlyPolicy(self.root).identity)
        self.assertNotEqual(policy.identity, ReadOnlyPolicy(self.root, ['source']).identity)

    def test_root_dominance_still_validates_every_lexical_scope(self):
        invalid = ('./', './file', 'source/.', '..', '../other', 'source/../other',
                   '.hidden', 'source/.hidden', '/absolute', 'a//b', 'a/', '', 'a\\b',
                   'nul\x00suffix', 'x' * 513, '/'.join(['a'] * 33), None, 3, Path('source'))
        for value in invalid:
            for paths in (['.', value], [value, '.']):
                with self.subTest(paths=paths), self.assertRaisesRegex(ValueError, '^invalid_read_only_path$'):
                    self.whole(paths)
        with self.assertRaisesRegex(ValueError, '^invalid_read_only_paths$'):
            self.whole(['.'] + ['source'] * 32)

    def test_whole_validation_does_not_scan_hidden_linked_special_or_large_trees(self):
        hidden = self.root / '.hidden'; hidden.mkdir()
        (hidden / 'link').symlink_to(self.root / 'source')
        os.link(self.root / 'source' / 'code.py', self.root / 'hard-alias')
        os.mkfifo(self.root / 'fifo')
        with patch('agent_lab.permissions.os.scandir', side_effect=AssertionError('whole-root policy must not enumerate project')):
            policy = self.whole(['.', 'source/code.py', 'future/file'])
            policy.validate()
        self.assertEqual(policy.paths, ('.',))

    def test_whole_root_must_exist_and_be_a_real_directory(self):
        for root in (self.root / 'missing', self.root / 'source' / 'code.py'):
            with self.subTest(root=root), self.assertRaisesRegex(ValueError, '^invalid_read_only_path$'):
                ReadOnlyPolicy(root, ['.'])
        link = self.root.parent / 'root-link'; link.symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, '^invalid_read_only_path$'):
            ReadOnlyPolicy(link, ['.'])
        policy = self.whole()
        self.root.rename(self.root.parent / 'old-workspace')
        self.root.symlink_to(self.root.parent / 'old-workspace')
        with self.assertRaisesRegex(ValueError, '^invalid_read_only_path$'): policy.validate()

    def test_every_visible_mutation_is_denied_and_ancestors_stop_at_root(self):
        policy = self.whole()
        for path in ('source/code.py', 'new', 'future/child', 'SOURCE/CODE.py', '目录/新文件'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, '^read_only_path$'):
                policy.check_mutation(path)
        self.assertEqual(policy.protected_ancestors(), [self.root])
        self.assertFalse((self.root / 'new').exists())
        self.assertEqual((self.root / 'source' / 'code.py').read_text(), 'original')

    def test_scratch_overlap_is_rejected_in_both_directions(self):
        policy = self.whole()
        for scratch in (self.root, self.root / 'private-scratch', self.root / 'future' / 'nested',
                        self.root.parent, self.root / 'source' / '..'):
            with self.subTest(scratch=scratch), self.assertRaisesRegex(ValueError, '^read_only_scratch_overlap$'):
                policy.check_scratch(scratch)
        outside = self.root.parent / 'scratch'; outside.mkdir()
        self.assertIsNone(policy.check_scratch(outside))
        self.assertIsNone(policy.check_scratch(self.root.parent / 'Workspace-sibling'))

    def test_scratch_symlink_case_and_unicode_aliases_are_conservatively_rejected(self):
        policy = self.whole()
        link = self.root.parent / 'scratch-alias'; link.symlink_to(self.root)
        for scratch in (link, link / 'nested', self.root.with_name('WORKSPACE') / 'nested'):
            with self.subTest(scratch=scratch), self.assertRaisesRegex(ValueError, '^read_only_scratch_overlap$'):
                policy.check_scratch(scratch)
        unicode_root = self.root.parent / 'Café'; unicode_root.mkdir()
        unicode_policy = ReadOnlyPolicy(unicode_root, ['.'])
        alias = unicode_root.with_name(unicodedata.normalize('NFD', 'CAFÉ'))
        with self.assertRaisesRegex(ValueError, '^read_only_scratch_overlap$'):
            unicode_policy.check_scratch(alias / 'nested')
        with self.assertRaisesRegex(ValueError, '^read_only_native_path_unsupported$'):
            unicode_policy.check_native()

    def test_existing_empty_and_scoped_policies_keep_identity_scan_and_scratch_behavior(self):
        empty = ReadOnlyPolicy(self.root)
        scoped = ReadOnlyPolicy(self.root, ['source/code.py', 'source'])
        self.assertFalse(empty.whole_workspace)
        self.assertFalse(scoped.whole_workspace)
        self.assertEqual(empty.manifest, {'version': 1, 'read_only_paths': []})
        self.assertEqual(scoped.paths, ('source',))
        self.assertIsNone(scoped.check_scratch(self.root / 'source'))
        self.assertIsNone(empty.check_scratch(None))
        self.assertIsNone(scoped.check_mutation('allowed'))
        with self.assertRaisesRegex(ValueError, '^read_only_path$'): scoped.check_mutation('source/code.py')
        (self.root / 'source' / 'link').symlink_to(self.root / 'source' / 'code.py')
        with self.assertRaisesRegex(ValueError, '^invalid_read_only_path$'): scoped.validate()


if __name__ == '__main__': unittest.main()
