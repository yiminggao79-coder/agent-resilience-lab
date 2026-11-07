"""Independent known-read boundaries with synthetic files and scripted samples."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import file_context
from agent_lab.coding_tools import CodingTools
from agent_lab.compaction import source_hash, validate_record
from agent_lab.engine import Engine
from agent_lab.store import Store
from tests.support import ScriptedModel, tool, final


def sha(data):
    return hashlib.sha256(data).hexdigest()


def notices(state):
    return [message for message in state['messages'] if '_file_context' in message]


def payload(message):
    return json.loads(message['content'].split('\n',1)[1])


class FileContextReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base/'work'; self.root.mkdir()
        self.tools = CodingTools(self.root)
        self.store = Store(self.base/'state.sqlite'); self.addCleanup(self.store.close)

    def test_confirmed_write_is_not_promoted_to_a_read_baseline(self):
        path = self.root/'source.txt'; path.write_bytes(b'OLD_PRIVATE_VALUE\n')
        captured = []
        store = self.store

        class CapturingModel(ScriptedModel):
            def complete(self,*args,**kwargs):
                captured.append(store.load(task))
                return super().complete(*args,**kwargs)

        model = CapturingModel([
            tool('read_file',{'path':'source.txt'}),
            tool('write_file',{'path':'source.txt','content':'NEW_PRIVATE_VALUE\n'}),
            tool('read_lines',{'path':'source.txt','start_line':1,'end_line':1}),
            final(),
        ])
        engine = Engine(self.store,self.tools,model)
        task = engine.create('Read, write and re-read',track_file_context=True)
        state = engine.run(task)
        self.assertEqual(state['phase'],'completed')
        after_write = captured[2]
        self.assertEqual(after_write['file_context']['reads'][0]['sha256'],sha(b'OLD_PRIVATE_VALUE\n'))
        changed = payload(notices(after_write)[-1])['files'][0]
        self.assertEqual((changed['status'],changed['read_sha256'],changed['current_sha256']),
                         ('changed',sha(b'OLD_PRIVATE_VALUE\n'),sha(b'NEW_PRIVATE_VALUE\n')))
        reread = state['file_context']['reads'][0]
        self.assertEqual((reread['source'],reread['sha256']),('read_lines',sha(b'NEW_PRIVATE_VALUE\n')))
        self.assertEqual(json.loads(state['messages'][reread['message_index']]['content'])['sha256'],reread['sha256'])
        self.assertEqual(payload(notices(state)[-1])['files'][0]['status'],'same')
        self.assertEqual(len(notices(state)),2)
        self.assertEqual(state['messages'][:len(after_write['messages'])],after_write['messages'])
        self.assertNotIn('source.txt',json.dumps(self.store.events(task)))
        self.assertNotIn('PRIVATE_VALUE',json.dumps(self.store.events(task)))

    def test_explicit_compaction_and_fork_preserve_archive_but_use_latest_snapshot(self):
        path = self.root/'source.txt'; old = b'OLD_PRIVATE_VALUE\n'+b'x'*10000+b'\n'
        path.write_bytes(old)
        (self.root/'other.txt').write_bytes(b'other\n')
        edited = []

        def external_edit(kind,state):
            if kind=='after_tool_effect' and not edited:
                edited.append(True); path.write_bytes(b'NEW_PRIVATE_VALUE\n'+b'y'*10000+b'\n')

        engine = Engine(self.store,self.tools,ScriptedModel([
            tool('read_file',{'path':'source.txt'}),tool('read_file',{'path':'other.txt'}),final()
        ]),hook=external_edit)
        task = engine.create('Read two sources',track_file_context=True)
        self.assertEqual(engine.run(task)['phase'],'completed')
        engine.model = ScriptedModel([{**final('Historical synthetic summary.'),'finish_reason':'stop'}])
        compacted = engine.compact(task)
        self.assertEqual(compacted['last_compaction']['status'],'installed')
        source = copy.deepcopy(compacted)
        end = source['semantic_context']['covered_until']
        prefix = copy.deepcopy(source['messages'][:end])
        child = engine.fork_session(task,'Continue the shared-file analysis')
        path.unlink()
        model = ScriptedModel([final()]); engine.model = model
        state = engine.run(child)
        self.assertEqual(state['phase'],'completed')
        self.assertEqual(self.store.load(task),source)
        self.assertEqual(state['messages'][:end],prefix)
        self.assertEqual(source_hash(state['messages'],end),source['semantic_context']['source_sha256'])
        self.assertEqual(validate_record(state['messages'],state['semantic_context'],model.identity),end)
        self.assertEqual(next(entry for entry in state['file_context']['reads'] if entry['path']=='source.txt')['sha256'],sha(old))
        latest = notices(state)[-1]
        self.assertEqual(next(item for item in payload(latest)['files'] if item['path']=='source.txt')['status'],'deleted')
        seen = [message.get('content') for message in model.seen[0]]
        self.assertIn(latest['content'],seen)
        for historical in notices(state)[:-1]:
            self.assertNotIn(historical['content'],seen)
        self.assertFalse(any('_file_context' in message or '_runtime_control' in message for message in model.seen[0]))

    def test_nested_parent_symlink_is_unavailable_without_reading_outside_scoped_policy(self):
        parent = self.root/'safe'; parent.mkdir()
        (parent/'source.txt').write_bytes(b'original\n')
        protected = CodingTools(self.root,read_only_paths=['safe/source.txt'])
        intent = protected._prepare('read_file',{'path':'safe/source.txt'})
        result = protected._execute(intent,2)
        record = file_context.record_read(file_context.new_record(),intent,result,0)
        outside = self.base/'outside'; outside.mkdir()
        (outside/'source.txt').write_bytes(b'PRIVATE_EXTERNAL_CONTENT')
        saved = self.root/'saved-safe'; parent.rename(saved); parent.symlink_to(outside)
        with patch.object(protected,'_read_at',side_effect=AssertionError('external content must not be read')) as read:
            observed,notice,metadata = file_context.inspect(protected,record)
        read.assert_not_called()
        item = json.loads(notice.split('\n',1)[1])['files'][0]
        self.assertEqual((item['status'],item['current_sha256']),('unavailable',None))
        self.assertEqual(observed['reads'],record['reads'])
        self.assertNotIn('PRIVATE_EXTERNAL_CONTENT',notice)
        self.assertEqual(set(metadata),{'checked_files','file_notices','retained_bytes'})
        self.assertNotIn('safe',json.dumps(metadata))
        parent.unlink(); saved.rename(parent)
        restored,notice,_ = file_context.inspect(protected,observed)
        self.assertEqual(json.loads(notice.split('\n',1)[1])['files'][0]['status'],'same')
        self.assertEqual(restored['reads'],record['reads'])
