"""Bounded file deletion contract, no model API."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from agent_lab.coding_tools import CodingTools
from agent_lab.tools import ToolError,UncertainEffect


class LargeDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir();self.tools=CodingTools(self.root)

    def test_exact_two_mib_binary_deletion_has_small_intent_and_absence_reconciliation(self):
        path=self.root/'large';path.write_bytes(b'\x00\xff'*(1024*1024));path.chmod(0o755)
        intent=self.tools.prepare('delete_file',{'path':'large'})
        self.assertLess(len(json.dumps(intent)),1024);self.assertEqual(intent['before_mode'],0o755)
        self.assertTrue(path.exists());self.assertFalse(self.tools.execute(intent,5)['reconciled'])
        self.assertFalse(path.exists());self.assertTrue(self.tools.recover(intent,0)['reconciled'])
        schema=next(s for s in self.tools.schemas() if s['function']['name']=='delete_file')
        self.assertIn('2 MiB',schema['function']['description'])

    def test_over_two_mib_rejected_without_effect(self):
        path=self.root/'large';path.write_bytes(b'x'*(2*1024*1024+1))
        with self.assertRaises(ToolError) as error:self.tools.prepare('delete_file',{'path':'large'})
        self.assertEqual(error.exception.code,'file_too_large');self.assertEqual(path.stat().st_size,2*1024*1024+1)

    def test_large_changed_content_or_mode_is_preserved(self):
        for changed in ('content','mode'):
            with self.subTest(changed=changed):
                path=self.root/changed;path.write_bytes(b'x'*210001);path.chmod(0o755)
                intent=self.tools.prepare('delete_file',{'path':changed})
                if changed=='content':path.write_bytes(b'y'*210001)
                else:path.chmod(0o700)
                before=path.stat()
                with self.assertRaises(UncertainEffect):self.tools.recover(intent,5)
                after=path.stat();self.assertEqual((before.st_ino,before.st_mtime_ns,before.st_mode),(after.st_ino,after.st_mtime_ns,after.st_mode))

    def test_recreated_large_target_with_changed_mode_is_not_deleted(self):
        path=self.root/'large';data=b'x'*210001;path.write_bytes(data);path.chmod(0o755)
        intent=self.tools.prepare('delete_file',{'path':'large'});self.tools.execute(intent,5)
        path.write_bytes(data);path.chmod(0o700);before=path.stat()
        with self.assertRaises(UncertainEffect):self.tools.recover(intent,5)
        self.assertEqual(path.stat().st_ino,before.st_ino);self.assertEqual(path.read_bytes(),data)

    def test_large_delete_keeps_host_readonly_boundary(self):
        path=self.root/'large';path.write_bytes(b'x'*210001)
        intent=self.tools.prepare('delete_file',{'path':'large'})
        protected=CodingTools(self.root,read_only_paths=['large'])
        with self.assertRaises(ToolError):protected.prepare('delete_file',{'path':'large'})
        with self.assertRaises(ToolError):protected.execute(intent,5)
        self.assertTrue(path.exists())

    def test_legacy_file_and_patch_bounds_are_not_expanded(self):
        path=self.root/'large';path.write_text('x'*65537)
        with self.assertRaises(ToolError) as error:
            self.tools.execute(self.tools.prepare('read_file',{'path':'large'}),3)
        self.assertEqual(error.exception.code,'file_too_large')
        for name,args in [('write_file',{'path':'large','content':'small'}),
                          ('apply_patch',{'path':'large','old':'xx','new':'y'}),
                          ('apply_patch_text',{'patch':'*** Begin Patch\n*** Delete File: large\n*** End Patch'})]:
            with self.subTest(name=name),self.assertRaises(ToolError):self.tools.prepare(name,args)
        self.assertEqual(path.stat().st_size,65537)
