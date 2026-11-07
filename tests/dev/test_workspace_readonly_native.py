"""Actual nested Seatbelt whole-root protection; no API or frozen fixtures."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from agent_lab import sandbox
from agent_lab.coding_tools import CodingTools
from agent_lab.tools import ToolError


class WorkspaceReadonlyNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=(Path(self.tmp.name)/'work').resolve();self.root.mkdir()
        (self.root/'keep.txt').write_text('original')
        (self.root/'keep.txt').chmod(0o640)
        (self.root/'é.txt').write_text('unicode original')
        self.tools=CodingTools(self.root,read_only_paths=['.'])

    def test_native_project_writes_denied_but_reads_and_private_scratch_work(self):
        body="""import os,json,fcntl,pathlib
p=pathlib.Path('keep.txt');blocked=[]
actions={'write':lambda:p.write_text('bad'),'create':lambda:pathlib.Path('new.txt').write_text('bad'),
 'unlink':lambda:p.unlink(),'rename':lambda:p.rename('moved.txt'),'chmod':lambda:p.chmod(0o600),
 'link':lambda:os.link('keep.txt','linked.txt'),
 'unicode':lambda:pathlib.Path('e\\u0301.txt').write_text('bad')}
for label,action in actions.items():
 try:action()
 except PermissionError:blocked.append(label)
for operation in (80,110):
 fd=os.open('keep.txt',os.O_RDONLY)
 try:
  try:fcntl.fcntl(fd,operation,0)
  except PermissionError:blocked.append('fcntl'+str(operation))
 finally:os.close(fd)
assert len(blocked)==9,blocked
scratch=pathlib.Path(os.environ['TMPDIR'])/'private-output'
scratch.write_text('scratch allowed')
assert scratch.read_text()=='scratch allowed'
assert p.read_text()=='original'
assert pathlib.Path(os.environ['HOME'])==scratch.parent
print(json.dumps({'blocked':sorted(blocked),'scratch_ok':True,'read_ok':True}))
"""
        intent=self.tools.prepare('run_command',{'argv':['python','-c',body],'cwd':'.'})
        result=self.tools.execute(intent,6)
        self.assertTrue(result['ok'],result)
        observed=json.loads(result['stdout']);self.assertEqual(len(observed['blocked']),9)
        self.assertTrue(observed['scratch_ok']);self.assertTrue(observed['read_ok'])
        self.assertEqual((self.root/'keep.txt').read_text(),'original')
        self.assertEqual((self.root/'keep.txt').stat().st_mode&0o777,0o640)
        self.assertEqual((self.root/'é.txt').read_text(),'unicode original')
        self.assertFalse((self.root/'new.txt').exists());self.assertFalse((self.root/'linked.txt').exists())

    def test_unittest_runs_with_project_readonly_and_external_temp_writable(self):
        (self.root/'test_readonly.py').write_text("""import unittest,pathlib,os
class T(unittest.TestCase):
 def test_policy(self):
  with self.assertRaises(PermissionError):pathlib.Path('keep.txt').write_text('bad')
  with self.assertRaises(PermissionError):pathlib.Path('created').mkdir()
  scratch=pathlib.Path(os.environ['TMPDIR'])/'test-temp'
  scratch.write_text('temporary');self.assertEqual(scratch.read_text(),'temporary')
  self.assertEqual(pathlib.Path('keep.txt').read_text(),'original')
""")
        result=self.tools.execute(self.tools.prepare('run_tests',{}),6)
        self.assertTrue(result['ok'],result)
        self.assertEqual((self.root/'keep.txt').read_text(),'original')
        self.assertFalse((self.root/'created').exists())

    def test_profile_refuses_overlapping_scratch_before_native_launch(self):
        for scratch in (self.root,self.root/'scratch',self.root.parent):
            with self.subTest(scratch=scratch):
                with self.assertRaises(ToolError) as caught:
                    sandbox._profile(self.root,scratch,read_only_paths=['.'])
                self.assertEqual(caught.exception.code,'read_only_scratch_overlap')
