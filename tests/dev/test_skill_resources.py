"""Project-only supporting text/script snapshot contracts, no provider calls."""
import hashlib
import json
import os
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import instructions
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import ToolError
from tests.support import ScriptedModel,tool,final


class SkillResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-skill-resource-',dir='/private/tmp');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir()
        self.skill=self.root/'.agents/skills/owned';self.skill.mkdir(parents=True)
        self.document=self.skill/'SKILL.md';self.document.write_text('---\nname: owned\ndescription: Owned reusable resource procedure\n---\nRead references/rules.md and use scripts/helper.py.\n')
        (self.skill/'references').mkdir();self.resource=self.skill/'references/rules.md';self.resource.write_text('owned reference\n')
        (self.skill/'scripts').mkdir();self.script=self.skill/'scripts/helper.py';self.script.write_text('from __future__ import annotations\nprint("owned helper")\n')

    def test_full_text_hash_and_procedural_boundary_without_changing_old_main_loader(self):
        from agent_lab.skill_resources import read_skill_resource
        before=instructions.load_skill(self.root,'owned')
        result=read_skill_resource(self.root,'owned','references/rules.md')
        self.assertEqual(result['content'],'owned reference\n');self.assertEqual(result['sha256'],hashlib.sha256(self.resource.read_bytes()).hexdigest())
        self.assertEqual(result['skill_sha256'],before['sha256']);self.assertEqual(result['path'],'.agents/skills/owned/references/rules.md')
        self.assertIn('never grants permissions',result['guidance'])
        self.assertEqual(instructions.load_skill(self.root,'owned'),before)

    def test_disabled_invocation_requires_host_selection_not_model_explicit_argument(self):
        from agent_lab.skill_resources import read_skill_resource
        self.document.write_text(self.document.read_text().replace('---\nRead','disable-model-invocation: true\n---\nRead'))
        with self.assertRaisesRegex(instructions.InstructionError,'skill_model_invocation_disabled'):read_skill_resource(self.root,'owned','references/rules.md')
        self.assertTrue(read_skill_resource(self.root,'owned','references/rules.md',explicit=True)['ok'])
        with Store(self.base/'state.sqlite') as store:
            engine=Engine(store,CodingTools(self.root),ScriptedModel([]))
            for name,args in [('read_skill_resource',{'name':'owned','resource':'references/rules.md','explicit':True}),
                              ('run_skill_script',{'name':'owned','resource':'scripts/helper.py','args':[],'cwd':'.','explicit':True})]:
                with self.assertRaises(ToolError):engine._prepare_tool(name,args,3)

    def test_paths_links_and_nonregular_files_cannot_escape_skill_namespace(self):
        from agent_lab.skill_resources import read_skill_resource
        outside=self.base/'private';outside.write_text('private sentinel')
        (self.skill/'references/link').symlink_to(outside);os.link(outside,self.skill/'references/hard')
        os.mkfifo(self.skill/'references/pipe');(self.skill/'linked').symlink_to(self.base,target_is_directory=True)
        bad=['../private','/private/tmp/private','.hidden','references/../SKILL.md','references//rules.md','references\\rules.md',
             'references/new\nline','references/link','references/hard','references/pipe','references','linked/private']
        for path in bad:
            with self.subTest(path=path),self.assertRaises(instructions.InstructionError):read_skill_resource(self.root,'owned',path)
        with self.assertRaises(instructions.InstructionError):read_skill_resource(self.root,'../owned','references/rules.md')

    def test_invalid_text_and_oversize_fail_without_truncated_success(self):
        from agent_lab.skill_resources import read_skill_resource
        for raw in (b'\xff',b'x'*16385):
            self.resource.write_bytes(raw)
            with self.subTest(size=len(raw)),self.assertRaises(instructions.InstructionError):read_skill_resource(self.root,'owned','references/rules.md')

    def test_change_during_fd_read_is_detected_even_with_same_size_and_restored_mtime(self):
        from agent_lab.skill_resources import read_skill_resource
        before=self.resource.stat();real_read=os.read;mutations=[]
        def change(descriptor,size):
            data=real_read(descriptor,size)
            if data and not mutations and os.fstat(descriptor).st_ino==before.st_ino:
                self.resource.write_bytes(b'x'*before.st_size)
                os.utime(self.resource,ns=(before.st_atime_ns,before.st_mtime_ns));mutations.append(True)
            return data
        with patch('agent_lab.skill_resources.os.read',side_effect=change):
            with self.assertRaisesRegex(instructions.InstructionError,'^skill_resource_changed$'):
                read_skill_resource(self.root,'owned','references/rules.md')
        self.assertEqual(mutations,[True])

    def test_engine_resource_result_uses_normal_read_feedback_and_metadata_trace_excludes_body(self):
        with Store(self.base/'state.sqlite') as store:
            engine=Engine(store,CodingTools(self.root),ScriptedModel([tool('read_skill_resource',{'name':'owned','resource':'references/rules.md'},'resource'),final()]))
            state=engine.run(engine.create('Read owned supporting resource'))
            self.assertEqual(state['phase'],'completed')
            result=json.loads(next(m['content'] for m in state['messages'] if m['role']=='tool'))
            self.assertTrue(result['ok'],result);self.assertEqual(result['content'],'owned reference\n')
            self.assertNotIn('owned reference',json.dumps(store.events(state['id'])))
            schema=next(s['function'] for s in engine._schemas() if s['function']['name']=='run_skill_script')
            self.assertEqual(set(schema['parameters']['required']),{'name','resource','args','cwd'})

    def test_script_preparation_is_existing_canonical_command_with_approved_source_snapshot(self):
        with Store(self.base/'state.sqlite') as store:
            engine=Engine(store,CodingTools(self.root),None)
            intent=engine._prepare_tool('run_skill_script',{'name':'owned','resource':'scripts/helper.py','args':['owned'],'cwd':'.'},3)
            self.assertEqual((intent['name'],intent['policy']),('run_command','unsafe'))
            self.assertIn(hashlib.sha256(self.script.read_bytes()).hexdigest(),intent['argv'][3])
            self.assertEqual(intent['argv'][1:3],['-I','-c']);self.assertEqual(intent['argv'][-1],'owned')
            self.script.write_text('print("changed after prepare")\n')
            self.assertNotIn('changed after prepare',intent['argv'][3])

    def test_saved_host_selection_survives_a_new_engine_without_repeating_cli_skill(self):
        self.document.write_text(self.document.read_text().replace('---\nRead','disable-model-invocation: true\n---\nRead'))
        with Store(self.base/'state.sqlite') as store:
            selected=CodingTools(self.root);selected.selected_skills=['owned']
            task=Engine(store,selected,ScriptedModel([])).create('Use explicitly selected owned helper')
            model=ScriptedModel([tool('run_skill_script',{'name':'owned','resource':'scripts/helper.py','args':[],'cwd':'.'},'helper'),final()])
            state=Engine(store,CodingTools(self.root),model).run(task)
            self.assertEqual(state['phase'],'awaiting_approval',state.get('reason'))

    def test_prepared_source_remains_recoverable_in_archive_after_source_removal_and_denial(self):
        from agent_lab.history import lookup_message as lookup
        from agent_lab.context import build_context
        self.script.write_text(self.script.read_text()+'# PRIVATE_SKILL_BODY_ONLY_71e4\n')
        original=self.script.read_text()
        with Store(self.base/'state.sqlite') as store:
            engine=Engine(store,CodingTools(self.root),ScriptedModel([tool('run_skill_script',{'name':'owned','resource':'scripts/helper.py','args':[],'cwd':'.'},'helper')]))
            state=engine.run(engine.create('Prepare owned helper, do not execute'))
            self.assertEqual(state['phase'],'awaiting_approval')
            self.script.unlink()
            approval=engine.approval_request(state['id'])
            state=engine.decide_approval(state['id'],approval['request_id'],approval['digest'],False)
            index=len(state['messages'])-1;offset=0;parts=[]
            while True:
                page=lookup(state['messages'],index,offset,8192);parts.append(page['fragment'])
                if page['complete']:break
                offset=page['next_offset']
            archived=json.loads(''.join(parts))
            self.assertEqual(archived['skill_script_snapshot']['content'],original)
            self.assertEqual(archived['skill_script_snapshot']['purpose'],'prepared_command_source')
            view,_=build_context(state['messages'],engine._schemas(),262144)
            self.assertNotIn('_skill_script_snapshot',json.dumps(view))
            self.assertNotIn('PRIVATE_SKILL_BODY_ONLY_71e4',json.dumps(view))
            self.assertNotIn('PRIVATE_SKILL_BODY_ONLY_71e4',json.dumps(store.events(state['id'])))

    def test_public_event_reader_retains_only_real_source_hashes_not_null_or_other_values(self):
        from agent_lab.event_reader import read_events
        with Store(self.base/'state.sqlite') as store:
            task=Engine(store,CodingTools(self.root),ScriptedModel([])).create('Owned source trace')
            for value in (None,'bad',True,1,'g'*64,'a'*64):
                store.db.execute('INSERT INTO events(task_id,at,kind,meta) VALUES(?,?,?,?)',
                    (task,time.time(),'synthetic',json.dumps({'source_sha256':value})))
            store.db.commit()
            rows=[event for event in read_events(store,task)['events'] if event['kind']=='synthetic']
            for row in rows[:-1]:
                self.assertNotIn('source_sha256',row['metadata']);self.assertIn('filtered_metadata',row['diagnostics'])
            self.assertEqual(rows[-1]['metadata']['source_sha256'],'a'*64)


if __name__=='__main__':unittest.main()
