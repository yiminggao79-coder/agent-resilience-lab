"""Scripted decisions verify CLI policy forwarding; not real LLM scores."""
from contextlib import redirect_stdout,redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.store import Store
from tests.dev.test_read_only_cli import Model
from tests.support import tool,final


class WorkspaceReadonlyCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir()
        (self.root/'keep.txt').write_text('original')
        self.db=Path(self.tmp.name)/'state.sqlite'

    def call(self,args,model=None):
        out,err=io.StringIO(),io.StringIO()
        with patch('agent_lab.__main__.OpenAIModel',return_value=model or Model([])),patch.dict(os.environ,{'SYNTHETIC_KEY':'synthetic-only'}),redirect_stdout(out),redirect_stderr(err):
            code=main(['--db',str(self.db),*args])
        return code,out.getvalue(),err.getvalue()

    def test_whole_root_policy_blocks_file_tool_and_persists_on_resume(self):
        model=Model([tool('write_file',{'path':'free.txt','content':'must not exist'}),
                     tool('read_file',{'path':'keep.txt'}),final('done')])
        common=['--workspace',str(self.root),'--allow-api','--model','synthetic']
        code,out,err=self.call(['run','Analyze only',*common,'--read-only-path','.'],model)
        self.assertEqual(code,0,err)
        self.assertFalse((self.root/'free.txt').exists())
        self.assertEqual((self.root/'keep.txt').read_text(),'original')
        with Store.open_readonly(self.db) as store:
            tid=store.db.execute('SELECT id FROM tasks').fetchone()[0];state=store.load(tid)
            self.assertEqual(state['read_only_policy'],{'version':1,'read_only_paths':['.']})
            self.assertEqual(state['phase'],'completed')
        code,out,err=self.call(['resume',tid,*common]);self.assertEqual(code,2)
        code,out,err=self.call(['resume',tid,*common,'--read-only-path','.']);self.assertEqual(code,0,err)

    def test_explicit_command_approval_does_not_grant_project_write_exception(self):
        code="""from pathlib import Path
try:Path('keep.txt').write_text('bad')
except PermissionError:print('project write denied after approval')
else:raise AssertionError('approval bypassed readonly root')
"""
        common=['--workspace',str(self.root),'--allow-api','--model','synthetic']
        status,out,err=self.call(['run','Inspect with command',*common,'--read-only-path','.'],
            Model([tool('run_command',{'argv':['python','-c',code],'cwd':'.'})]))
        self.assertEqual(status,3,err)  # Existing CLI waiting-for-approval code.
        with Store.open_readonly(self.db) as store:
            tid=store.db.execute('SELECT id FROM tasks').fetchone()[0];state=store.load(tid)
            self.assertEqual(state['phase'],'awaiting_approval')
            approval=state['pending']['approval']
        base=['approve',tid,approval['request_id'],'--digest',approval['digest'],'--workspace',str(self.root)]
        status,out,err=self.call(base);self.assertEqual(status,2)
        status,out,err=self.call([*base,'--read-only-path','.']);self.assertEqual(status,0,err)
        status,out,err=self.call(['resume',tid,*common,'--read-only-path','.'],Model([final('done')]))
        self.assertEqual(status,0,err)
        self.assertEqual((self.root/'keep.txt').read_text(),'original')
        with Store.open_readonly(self.db) as store:
            state=store.load(tid);self.assertEqual(state['phase'],'completed')
            self.assertTrue(state['last_native_job']['local_quiescent'])
            results=[m['content'] for m in state['messages'] if m['role']=='tool']
            self.assertTrue(any('project write denied after approval' in text for text in results))
