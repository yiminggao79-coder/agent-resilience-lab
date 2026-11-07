"""Bounded host venv authority; no installed pytest, network or model API."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
import venv
from unittest.mock import patch

from agent_lab.tools import ToolError
try:
    from agent_lab.python_environment import environment_descriptor
except ImportError:
    environment_descriptor=None


class PythonEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.root=self.base/'work';self.root.mkdir()
        self.env=self.base/'env';venv.EnvBuilder(with_pip=False,symlinks=True).create(self.env)
        self.packages=self.env/'lib'/('python%d.%d'%sys.version_info[:2])/'site-packages'
        self.module=self.packages/'public_dependency.py';self.module.write_text('value=3\n')
    def descriptor(self,selected=None,root=None):
        self.assertIsNotNone(environment_descriptor,'environment authority not implemented')
        return environment_descriptor(root or self.root,selected or self.env)
    def test_valid_manifest_keeps_launcher_base_config_and_tree_distinct(self):
        result=self.descriptor()
        self.assertEqual(result['path'],str(self.env))
        self.assertEqual(result['launcher'],str(self.env/'bin'/'python'))
        self.assertEqual(result['base_executable']['path'],str(Path(sys.executable).resolve()))
        self.assertEqual(result['packages']['path'],str(self.packages))
        self.assertEqual(result['packages']['files'],1)
        self.assertEqual(len(result['packages']['sha256']),64)
        self.assertNotIn('value=3',str(result))
        self.assertEqual(result,self.descriptor())
    def test_targets_relative_alias_and_broad_overlap_are_rejected(self):
        alias=self.root/'alias';alias.symlink_to(self.env,target_is_directory=True)
        external=self.base/'env-alias';external.symlink_to(self.env,target_is_directory=True)
        inside=self.env/'project';inside.mkdir()
        for selected,root in [('relative',self.root),(alias,self.root),(external,self.root),(self.env,inside)]:
            with self.subTest(selected=selected),self.assertRaises(ToolError):self.descriptor(selected,root)
    def test_configuration_mismatch_and_retargeted_launcher_are_refused(self):
        cfg=self.env/'pyvenv.cfg';original=cfg.read_text()
        for text in (original.replace('false','true'),original.replace('version =','home ='),
                     original.replace('version =','version = 1.0\nprevious =')):
            cfg.write_text(text)
            with self.subTest(text=text),self.assertRaises(ToolError):self.descriptor()
        cfg.write_text(original)
        launcher=self.env/'bin'/'python';launcher.unlink();launcher.symlink_to('/usr/bin/true')
        with self.assertRaises(ToolError):self.descriptor()
    def test_content_replacement_and_modes_change_bound_identity(self):
        initial=self.descriptor();info=self.module.stat();data=self.module.read_bytes()
        self.module.unlink();self.module.write_bytes(data);os.utime(self.module,ns=(info.st_atime_ns,info.st_mtime_ns))
        replaced=self.descriptor();self.assertNotEqual(initial,replaced)
        self.module.write_text('value=4\n');self.assertNotEqual(replaced,self.descriptor())
        cfg=self.env/'pyvenv.cfg';before=self.descriptor();cfg.chmod(0o600)
        self.assertNotEqual(before,self.descriptor())
    def test_hidden_symlink_hardlink_and_special_package_entries_are_not_granted(self):
        for kind in ('hidden','symlink','hardlink','fifo'):
            path=self.packages/('.private' if kind=='hidden' else 'unsupported')
            if kind=='symlink':path.symlink_to(self.module)
            elif kind=='hardlink':os.link(self.module,path)
            elif kind=='fifo':os.mkfifo(path)
            else:path.write_text('not a dependency')
            with self.subTest(kind=kind),self.assertRaises(ToolError):self.descriptor()
            path.unlink()
    def test_byte_and_entry_caps_fail_without_unbounded_source_reads(self):
        self.assertIsNotNone(environment_descriptor)
        with patch('agent_lab.python_environment.MAX_PACKAGE_BYTES',4):
            with self.assertRaises(ToolError):self.descriptor()
        with patch('agent_lab.python_environment.MAX_PACKAGE_ENTRIES',0):
            with self.assertRaises(ToolError):self.descriptor()


if __name__=='__main__':unittest.main()
