"""Fork CLI creates local history branches without inference or replay."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.engine import Engine
from agent_lab.coding_tools import CodingTools
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel,final


class SessionForkCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir();self.db=Path(self.tmp.name)/'state.sqlite'

    def source(self,coding=False,readonly=()):
        tools=(CodingTools if coding else Tools)(self.root,read_only_paths=readonly)
        with Store(self.db) as store:
            engine=Engine(store,tools,ScriptedModel([final('PRIVATE assistant result')]))
            tid=engine.create('PRIVATE original task');engine.run(tid)
            return tid,store.load(tid),store.events(tid)

    def call(self,args):
        out,err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            try:code=main(['--db',str(self.db)]+args)
            except SystemExit as error:code=error.code
        return code,out.getvalue(),err.getvalue()

    def test_fork_outputs_only_new_ids_and_shared_workspace_metadata_without_model_or_execution(self):
        tid,source,events=self.source()
        with patch('agent_lab.__main__.OpenAIModel',side_effect=AssertionError('No model client')),\
             patch.object(Engine,'run',side_effect=AssertionError('No automatic run')),\
             patch.object(Tools,'execute',side_effect=AssertionError('No tool replay')):
            code,out,err=self.call(['fork',tid,'Try an alternative','--workspace',str(self.root)])
        self.assertEqual(code,0,err);result=json.loads(out)
        self.assertNotEqual(result['id'],tid);self.assertNotEqual(result['session_id'],source['session_id'])
        self.assertNotEqual(result['turn_id'],source['turn_id']);self.assertEqual(result['phase'],'ready')
        self.assertTrue(result['workspace_shared']);self.assertFalse(result['model_called'])
        self.assertEqual(result['source_revision'],source['revision']);self.assertEqual(result['copied_messages'],len(source['messages']))
        self.assertNotIn('PRIVATE',out);self.assertNotIn('Try an alternative',out)
        with Store.open_readonly(self.db) as store:
            self.assertEqual(store.load(tid),source);self.assertEqual(store.events(tid),events)
            child=store.load(result['id']);self.assertEqual(child['messages'][:len(source['messages'])],source['messages'])
            self.assertEqual(child['messages'][-1],{'role':'user','content':'Try an alternative'})

    def test_status_exposes_filtered_lineage_without_history_or_arbitrary_origin_values(self):
        tid,source,_=self.source()
        code,out,err=self.call(['fork',tid,'New task','--workspace',str(self.root)])
        self.assertEqual(code,0,err);child_id=json.loads(out)['id']
        code,out,err=self.call(['status',child_id]);self.assertEqual(code,0,err)
        origin=json.loads(out)['fork_origin']
        self.assertEqual(origin['source_task_id'],tid);self.assertEqual(origin['message_count'],len(source['messages']))
        self.assertTrue(origin['workspace_shared']);self.assertNotIn('source_session_metrics',origin)
        self.assertNotIn('PRIVATE',out)
        with Store(self.db) as store:
            child=store.load(child_id);child['fork_origin']['source_task_id']={'private':'PRIVATE injected value'}
            child['fork_origin']['unexpected']='PRIVATE arbitrary origin';store.save(child,'fixture')
        code,out,err=self.call(['status',child_id]);self.assertEqual(code,0,err)
        self.assertIsNone(json.loads(out)['fork_origin']['source_task_id']);self.assertNotIn('PRIVATE',out)

    def test_coding_profile_and_readonly_manifest_survive_without_mcp_or_credentials(self):
        (self.root/'keep').write_text('untouched');tid,source,_=self.source(True,('keep',))
        code,out,err=self.call(['fork',tid,'Different next step','--workspace',str(self.root),'--read-only-path','keep'])
        self.assertEqual(code,0,err)
        with Store.open_readonly(self.db) as store:
            child=store.load(json.loads(out)['id'])
            self.assertEqual(child['profile'],'coding');self.assertEqual(child['read_only_policy'],source['read_only_policy'])
        self.assertEqual((self.root/'keep').read_text(),'untouched')

    def test_wrong_workspace_or_missing_readonly_scope_rejects_before_child_creation(self):
        (self.root/'keep').write_text('untouched');tid,_,_=self.source(True,('keep',))
        other=Path(self.tmp.name)/'other';other.mkdir()
        with Store.open_readonly(self.db) as store:before=store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        for root in (other,self.root):
            with self.subTest(root=root):
                code,_,_=self.call(['fork',tid,'New','--workspace',str(root)])
                self.assertEqual(code,2)
        self.assertEqual(list(other.iterdir()),[])
        with Store.open_readonly(self.db) as store:self.assertEqual(store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],before)

    def test_active_or_unknown_source_rejects_without_creation_or_confirmation(self):
        tid,_,_=self.source()
        with Store(self.db) as store:
            state=store.load(tid);state.update(phase='needs_confirmation',outcome_unresolved=True)
            store.save(state,'fixture');before=store.load(tid)
        code,_,_=self.call(['fork',tid,'Continue','--workspace',str(self.root)])
        self.assertEqual(code,2)
        with Store.open_readonly(self.db) as store:
            self.assertEqual(store.load(tid),before);self.assertEqual(store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)
