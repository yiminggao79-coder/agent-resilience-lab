"""Runner contracts on unrelated synthetic fixtures; not live quality scores."""
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


def fixture(root):
    suite=root/'suite';suite.mkdir();(suite/'workspace').mkdir()
    (suite/'workspace/a.py').write_text('value=1\n');(suite/'task.md').write_text('Inspect and improve the project.')
    (suite/'manifest.json').write_text(json.dumps({'version':1,'cases':[]}))
    rows=[{'path':str(p.relative_to(root)),'bytes':p.stat().st_size,
           'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(suite.rglob('*')) if p.is_file()]
    frozen={'version':1,'files':rows,'inventory_sha256':hashlib.sha256(json.dumps(
        rows,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()}
    (suite/'FREEZE.json').write_text(json.dumps(frozen));return suite


class ContextMissionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('scripts.context_mission'), 'mission runner is missing')
        self.module=importlib.import_module('scripts.context_mission')

    def test_no_api_mode_never_reads_suite_or_key_and_creates_no_trial(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()) as output:
            root=Path(temp);before=list(root.iterdir())
            with patch.object(self.module,'load_first_line_key',side_effect=AssertionError('key read')), \
                    patch.object(self.module,'load_suite',side_effect=AssertionError('suite read')):
                code=self.module.main(['--suite',str(root/'absent')])
            self.assertEqual(code,0);self.assertEqual(list(root.iterdir()),before)
            self.assertEqual(json.loads(output.getvalue())['api_requests_sent'],0)

    def test_quality_is_external_and_unknown_observation_does_not_become_zero(self):
        row={'immutable_files_unchanged':True,'original_tests_exit_code':0,'all_tests_exit_code':0,
             'oracles_complete':True,'oracle_matches':True,'model_says_done':False}
        self.assertIs(self.module.grade_turn(row),True)
        row.update(oracle_matches=False,model_says_done=True)
        self.assertIs(self.module.grade_turn(row),False)
        row.update(oracles_complete=None,oracle_matches=None)
        self.assertIsNone(self.module.grade_turn(row))
        row['immutable_files_unchanged']=False
        self.assertIs(self.module.grade_turn(row),False)

    def test_freeze_verification_detects_modified_file_and_added_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);suite=fixture(root)
            self.assertEqual(self.module.verify_suite(suite,repository=root),
                             hashlib.sha256((suite/'FREEZE.json').read_bytes()).hexdigest())
            (suite/'task.md').write_text('Changed task')
            with self.assertRaises(ValueError):self.module.verify_suite(suite,repository=root)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);suite=fixture(root);(suite/'extra.py').write_text('print(1)')
            with self.assertRaises(ValueError):self.module.verify_suite(suite,repository=root)

    def test_tool_and_runner_subprocess_environments_exclude_real_key_variables(self):
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'fake-private','AGENT_LAB_TRANSPORT_KEY':'fake-private',
                                   'OPENAI_API_KEY':'fake-private','PATH':'/poison'}):
            selected=self.module.child_environment()
        self.assertNotIn('fake-private',json.dumps(selected));self.assertEqual(selected['PATH'],os.defpath)

    def test_actual_observer_uses_stdin_not_argv_with_clean_environment(self):
        with tempfile.TemporaryDirectory(prefix='mission-observe-') as temp:
            root=Path(temp)/'project';root.mkdir()
            (root/'probe.py').write_text('import os,sys\ndef observe(value):\n'
                ' return {"value":value,"key_absent":os.environ.get("DEEPSEEK_API_KEY") is None,'
                '"not_in_argv":value not in " ".join(sys.argv)}\n')
            result=self.module.observe_stdin(root,'probe','observe',[['PRIVATE_ORACLE_INPUT']],10)
            self.assertTrue(result['complete'])
            self.assertEqual(result['observations'],[{'value':'PRIVATE_ORACLE_INPUT','key_absent':True,'not_in_argv':True}])

    def test_owned_runner_is_actually_killed_after_its_marker(self):
        with tempfile.TemporaryDirectory(prefix='mission-kill-') as temp:
            root=Path(temp);marker=root/'marker.json';script=root/'child.py'
            script.write_text('import json,os,signal,sys\n'
                'open(sys.argv[1],"w").write(json.dumps({"pid":os.getpid()}))\n'
                'while True: signal.pause()\n')
            result=self.module.run_owned([sys.executable,str(script),str(marker)],root/'out.txt',root/'err.txt',
                                         marker=marker,timeout=10)
            self.assertTrue(result['injected']);self.assertEqual(result['returncode'],-9)
            self.assertEqual(result['pid'],json.loads(marker.read_text())['pid'])

    def test_wrong_marker_owner_never_authorizes_signalling_another_process(self):
        with tempfile.TemporaryDirectory(prefix='mission-marker-') as temp:
            root=Path(temp);marker=root/'marker.json';script=root/'child.py'
            script.write_text('import json,os,signal,sys\n'
                'open(sys.argv[1],"w").write(json.dumps({"pid":os.getppid()}))\n'
                'while True: signal.pause()\n')
            with self.assertRaisesRegex(ValueError,'fault_marker_owner_mismatch'):
                self.module.run_owned([sys.executable,str(script),str(marker)],root/'out',root/'err',
                                      marker=marker,timeout=10)

    def test_injected_offline_turn_exercises_real_tools_and_keeps_session_history(self):
        from agent_lab.store import Store
        from tests.support import ScriptedModel, final, tool
        with tempfile.TemporaryDirectory(prefix='mission-turn-') as temp:
            base=Path(temp);root=base/'project';root.mkdir()
            config={'workspace':str(root),'db':str(base/'tasks.sqlite'),'task':'Write a note.',
                    'action':'start','read_only_paths':[],'limits':{'max_steps':8,'max_seconds':60},
                    'receipt_dir':str(base/'receipts-1'),'allow_api':False}
            first=self.module.execute_turn(config,model=ScriptedModel([
                tool('write_file',{'path':'note.txt','content':'actual feedback'}),final()]))
            self.assertEqual((root/'note.txt').read_text(),'actual feedback')
            self.assertEqual(first['phase'],'completed')
            with Store(base/'tasks.sqlite') as store:before=store.load(first['id'])
            config.update(action='continue',task='Read the saved note.',task_id=first['id'],
                          receipt_dir=str(base/'receipts-2'))
            second=self.module.execute_turn(config,model=ScriptedModel([
                tool('read_file',{'path':'note.txt'}),final()]))
            self.assertEqual(second['session_id'],first['session_id'])
            self.assertNotEqual(second['turn_id'],first['turn_id'])
            with Store(base/'tasks.sqlite') as store:
                after=store.load(first['id'])
            self.assertEqual(after['messages'][:len(before['messages'])],before['messages'])

    def test_boolean_exit_codes_are_not_success_measurements(self):
        row={'immutable_files_unchanged':True,'original_tests_exit_code':False,'all_tests_exit_code':0,
             'oracles_complete':True,'oracle_matches':True}
        self.assertIsNone(self.module.grade_turn(row))

    def test_unsafe_artifact_never_invokes_candidate_code(self):
        with patch.object(self.module.sandbox,'run_tests',side_effect=AssertionError('executed')):
            result=self.module.judge_turn({},0,Path('/absent'),Path('/absent'),{},False)
        self.assertIsNone(result['quality_passed'])

    def test_packing_proof_uses_actual_eligible_input_and_installed_operation(self):
        messages=[{'role':'user','content':'Improve.'},{'role':'assistant','content':'x'*4000},
                  {'role':'assistant','content':'y'*4000},
                  {'role':'assistant','content':None,'tool_calls':[{'id':'one','type':'function',
                   'function':{'name':'read_file','arguments':'{"path":"a"}'}}]},
                  {'role':'tool','tool_call_id':'one','content':'actual'}]
        proof=self.module.packing_proof(messages,None,5500)
        self.assertTrue(proof['proper_subprefix']);self.assertGreater(proof['full_request_bytes'],5500)
        self.assertLessEqual(proof['request_bytes'],5500)
        self.assertLess(proof['covered_until'],proof['eligible_until'])

    def test_receipts_include_pre_kill_and_resume_without_inventing_unknown_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for segment,tokens in [('first-receipts',123),('resume-receipts',7)]:
                p=root/segment;p.mkdir();(p/'model-001-request.json').write_text('{"messages":[]}')
                (p/'model-001-response.json').write_text(json.dumps({'usage':{'input_tokens':tokens,'output_tokens':2},
                    'finish_reason':'stop','message':{'content':'done'}}))
            (root/'resume-receipts/model-002-request.json').write_text('{"messages":[]}')
            records=self.module.receipt_inventory(root)
            self.assertEqual(len(records),3)
            self.assertEqual({r['segment'] for r in records},{'first-receipts','resume-receipts'})
            self.assertEqual(sum((r['usage'] or {}).get('input_tokens',0) for r in records),130)
            self.assertEqual(records[-1]['outcome'],'unknown');self.assertIsNone(records[-1]['usage'])
            self.assertTrue(all(len(r['request_sha256'])==64 for r in records))
            total=self.module.usage_totals(records)
            self.assertEqual(total['unknown_usage_requests'],1)
            self.assertEqual(total['known_input_tokens'],130)
            self.assertAlmostEqual(total['known_peak_cache_miss_estimate_cny'],.000292)

    def test_missing_child_result_exports_pending_receipt_and_null_quality(self):
        with tempfile.TemporaryDirectory(prefix='mission-missing-result-') as temp:
            base=Path(temp);suite=base/'suite';suite.mkdir();(suite/'workspace').mkdir()
            (suite/'workspace/a.py').write_text('x=1\n');(suite/'task.md').write_text('Improve x.')
            case={'id':'unrelated','workspace_dir':'workspace','immutable_files':['a.py'],
                  'turns':[{'task':'task.md'}]}
            def failed_child(argv,*unused,**kw):
                config=json.loads(Path(argv[-1]).read_text());receipts=Path(config['receipt_dir'])
                receipts.mkdir();(receipts/'model-001-request.json').write_text('{"messages":[]}')
                return {'pid':123,'returncode':-9,'injected':False,'timed_out':True}
            with patch.object(self.module,'run_owned',side_effect=failed_child):
                result=self.module._run_case(suite,case,base/'trial')
            row=result['turns'][0]
            self.assertIsNone(row['quality_passed']);self.assertFalse(result['complete'])
            self.assertEqual(row['usage_totals']['requests'],1)
            self.assertEqual(row['usage_totals']['unknown_usage_requests'],1)
            self.assertEqual(row['model_receipts'][0]['outcome'],'unknown')
            self.assertTrue((base/'trial/turn-1/stage-evidence.json').is_file())

    def test_rollover_watermark_preserves_working_space_instead_of_half_fixed_overhead(self):
        from agent_lab.store import Store
        from agent_lab.engine import Engine
        from agent_lab.coding_tools import CodingTools
        from agent_lab.context import build_context
        from tests.support import ScriptedModel,final,tool
        from tests.dev.test_compaction import summary
        with tempfile.TemporaryDirectory(prefix='mission-watermark-') as temp:
            base=Path(temp);root=base/'project';root.mkdir()
            config={'workspace':str(root),'db':str(base/'tasks.sqlite'),'task':'Write a working note.',
                    'action':'start','read_only_paths':[],'limits':{'max_steps':8,'max_seconds':60,
                    'max_context_bytes':64000,'max_auto_compactions':1},
                    'receipt_dir':str(base/'first'),'allow_api':False}
            first=self.module.execute_turn(config,model=ScriptedModel([
                tool('write_file',{'path':'note.txt','content':'observed details '*300}),final()]))
            with Store(base/'tasks.sqlite') as store:
                old=store.load(first['id']);engine=Engine(store,CodingTools(root),ScriptedModel([]))
                _,stats=build_context(old['messages'],engine._schemas(),64000)
            config.update(action='continue',task='Continue and verify.',task_id=first['id'],
                          receipt_dir=str(base/'second'),rollover=True)
            second=self.module.execute_turn(config,model=ScriptedModel([summary(),final()]))
            self.assertEqual(second['limits']['auto_compact_bytes'],stats['retained_bytes'])
            self.assertEqual(second['phase'],'completed')
            self.assertIsNotNone(second['summary'])

    def test_model_cost_uses_selected_official_model_rates(self):
        records=[{'usage':{'input_tokens':1000,'output_tokens':100}}]
        result=self.module.usage_totals(records,model_name='deepseek-v4-pro')
        self.assertAlmostEqual(result['known_peak_cache_miss_estimate_cny'],.0117)
        self.assertEqual(result['pricing_cny_per_million'],{'input_peak_cache_miss':9,'output_peak':27})

    def test_independent_turns_honor_full_48k_protocol_and_one_selected_model(self):
        with tempfile.TemporaryDirectory(prefix='mission-protocol-') as temp:
            base=Path(temp);suite=base/'suite';suite.mkdir();(suite/'workspace').mkdir()
            (suite/'workspace/a.py').write_text('x=1\n');(suite/'task.md').write_text('Improve x.')
            case={'id':'unrelated','workspace_dir':'workspace','immutable_files':['a.py'],
                  'turns':[{'task':'task.md'} for _ in range(3)]}
            captures=[]
            def child(argv,*unused,**kw):
                config=json.loads(Path(argv[-1]).read_text());captures.append(config)
                self.module.atomic_json(Path(argv[-1]).parent/'result.json',{
                    'id':'synthetic','phase':'completed','pid':100+len(captures),'trace':[],
                    'safe_to_observe':True,'model_receipts':[]})
                return {'pid':100+len(captures),'returncode':0,'injected':False,'timed_out':False}
            with patch.object(self.module,'run_owned',side_effect=child),patch.object(
                    self.module,'judge_turn',return_value={'quality_passed':True}):
                result=self.module._run_case(suite,case,base/'trial',model_name='deepseek-v4-pro')
            self.assertEqual(len(captures),3)
            self.assertTrue(all(c['limits']['max_context_bytes']==48000 for c in captures))
            self.assertTrue(all(c['model']=='deepseek-v4-pro' for c in captures))
            self.assertFalse(result['complete'])  # No real summary/coding proof in a contract double.

    def test_frozen_acceptance_cannot_bypass_exposure_admission_as_development(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp).resolve();suite=base/'suite';suite.mkdir();(suite/'workspace').mkdir()
            (suite/'manifest.json').write_text(json.dumps({'runtime_profile':{
                'experiment_profile':'thinking128k','model':'deepseek-flash'}}))
            protocol=base/self.module.experiment_settings('thinking128k')['protocol_file']
            protocol.parent.mkdir();protocol.write_text('synthetic protocol')
            snapshot={'runtime_dirty':False,'source_sha256':{},'commit':'synthetic'}
            ledger=SimpleNamespace(report=lambda:{'halted':False})
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(self.module,'REPO',base))
                stack.enter_context(patch.object(self.module,'implementation_snapshot',return_value=snapshot))
                stack.enter_context(patch.object(self.module.BudgetLedger,'open_existing',return_value=ledger))
                stack.enter_context(patch.object(self.module,'load_suite',return_value=('freeze',[{'id':'case','workspace_dir':'workspace'}])))
                stack.enter_context(patch.object(self.module,'verify_suite',return_value='freeze'))
                stack.enter_context(patch.object(self.module,'_probe_backend',return_value=True))
                stack.enter_context(patch.object(self.module,'export_candidate'))
                run=stack.enter_context(patch.object(self.module,'_run_case',return_value={'complete':False,'turns':[]}))
                stack.enter_context(patch.object(self.module,'load_first_line_key',side_effect=AssertionError('key read')))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                with self.assertRaisesRegex(ValueError,'frozen_runtime_profile_mismatch'):
                    self.module.main(['--suite',str(suite),'--experiment-profile','thinking128k',
                                      '--allow-api','--prepaid-account','--development'])
                run.assert_not_called()
                self.assertFalse((base/'.lab/context-mission-exposures').exists())

    def test_frozen_runtime_cannot_silently_use_a_different_resource_profile(self):
        validate=getattr(self.module,'validate_frozen_runtime',None)
        self.assertTrue(callable(validate),'Missing frozen runtime admission gate')
        expected={'experiment_profile':'thinking128k','model':'deepseek-flash'}
        validate(expected,'thinking128k','deepseek-flash')
        validate(None,'legacy48k','deepseek-v4-pro')  # Existing suite has no new constraint.
        for profile,model in [('legacy48k','deepseek-flash'),('thinking128k','deepseek-v4-pro')]:
            with self.assertRaisesRegex(ValueError,'frozen_runtime_profile_mismatch'):
                validate(expected,profile,model)

    def test_named_thinking_profile_preflight_never_reads_key_or_suite(self):
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            with patch.object(self.module,'load_first_line_key',side_effect=AssertionError('key read')), \
                    patch.object(self.module,'load_suite',side_effect=AssertionError('suite read')):
                try:code=self.module.main(['--experiment-profile','thinking128k','--suite','/absent'])
                except SystemExit as error:code=error.code
        self.assertEqual(code,0)

    def test_named_profile_reaches_official_adapter_before_any_key_read(self):
        class Captured(Exception):pass
        captured=[]
        def factory(*args,**kwargs):
            captured.append((args,kwargs));raise Captured()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'project';workspace.mkdir()
            config={'workspace':str(workspace),'db':str(root/'tasks.sqlite'),
                    'receipt_dir':str(root/'receipts'),'allow_api':True,'prepaid_account':True,
                    'experiment_profile':'thinking128k'}
            with patch.object(self.module,'OpenAIModel',side_effect=factory),patch.object(
                    self.module,'load_first_line_key',side_effect=AssertionError('key read')):
                with self.assertRaises(Captured):self.module.execute_turn(config)
        args,kw=captured[0]
        self.assertEqual(args[1],'https://api.deepseek.com')
        self.assertEqual(kw['thinking'],'enabled');self.assertEqual(kw.get('reasoning_effort'),'low')

    def test_four_natural_turns_get_named_working_limits_without_fabricating_runtime_proof(self):
        with tempfile.TemporaryDirectory(prefix='mission-profile-') as temp:
            base=Path(temp);suite=base/'suite';suite.mkdir();(suite/'workspace').mkdir()
            (suite/'workspace/a.py').write_text('x=1\n');(suite/'task.md').write_text('Improve x.')
            case={'id':'unrelated','workspace_dir':'workspace','immutable_files':['a.py'],
                  'turns':[{'task':'task.md'} for _ in range(4)]}
            captures=[]
            def child(argv,*unused,**kw):
                config=json.loads(Path(argv[-1]).read_text());captures.append(config)
                self.module.atomic_json(Path(argv[-1]).parent/'result.json',{
                    'id':'synthetic','phase':'completed','pid':100+len(captures),'trace':[],
                    'safe_to_observe':True,'model_receipts':[]})
                return {'pid':100+len(captures),'returncode':0,'injected':False,'timed_out':False}
            with patch.object(self.module,'run_owned',side_effect=child),patch.object(
                    self.module,'judge_turn',return_value={'quality_passed':True}):
                try:result=self.module._run_case(suite,case,base/'trial',experiment_profile='thinking128k')
                except TypeError as error:raise AssertionError('Named profile must reach all four Turns') from error
            self.assertEqual(len(captures),4)
            self.assertTrue(all(c['experiment_profile']=='thinking128k' for c in captures))
            self.assertTrue(all(c['limits']['max_context_bytes']==131072 and
                                c['limits']['max_summary_input_bytes']==32000 and
                                c['limits']['max_output_tokens']==16384 and
                                c['limits']['model_timeout']==180 for c in captures))
            self.assertFalse(result['complete'])  # Configuration/doubles never establish real Coding.

    def test_continuity_binds_installed_note_to_request_selecting_final_code_change(self):
        note={'source_sha256':'a'*64,'summary_sha256':'b'*64,'covered_until':5,
              'request_model_call_number':2,'pid':123}
        installed={'kind':'compaction_installed','seq':10,'compaction_automatic':True,
                   'source_sha256':'a'*64,'summary_sha256':'b'*64,'covered_messages':5,
                   'compaction_generation':1,'turn_id':'earlier-turn'}
        started={'kind':'model_started','seq':30,'model_calls':2,'turn_id':'final-turn'}
        write={'kind':'tool_finished','seq':35,'tool':'write_file','ok':True,'turn_id':'final-turn','operation_id':'changed'}
        row={'trace':[started,write],'model_visible_summaries':[note],
             'executed_python_paths':['impl.py'],'mutation_evidence':[{'operation_id':'changed','changes':[
                {'path':'impl.py','before_sha256':'c'*64,'after_sha256':'d'*64,
                 'before_ast_sha256':'e'*64,'after_ast_sha256':'f'*64}]}]}
        proof=self.module.continuity_proof([row],[installed,started,write])
        self.assertTrue(proof['passed'])
        self.assertEqual(proof['coding_model_call'],2)
        self.assertEqual(proof['installed_seq'],10)
        for changed in ({**note,'summary_sha256':'c'*64},{**note,'request_model_call_number':1}):
            self.assertFalse(self.module.continuity_proof([{**row,'model_visible_summaries':[changed]}],
                             [installed,started,write])['passed'])
        self.assertFalse(self.module.continuity_proof([row],[{**installed,'seq':40},started,write])['passed'])

    def test_noop_docs_tests_and_unexecuted_code_cannot_count_as_implementation_change(self):
        note={'source_sha256':'a'*64,'summary_sha256':'b'*64,'covered_until':5,'request_model_call_number':2}
        installed={'kind':'compaction_installed','seq':10,'compaction_automatic':True,
                   'source_sha256':'a'*64,'summary_sha256':'b'*64,'covered_messages':5}
        started={'kind':'model_started','seq':30,'model_calls':2}
        write={'kind':'tool_finished','seq':35,'tool':'write_file','ok':True,'operation_id':'op'}
        row={'trace':[started,write],'model_visible_summaries':[note],'executed_python_paths':['impl.py'],
             'mutation_evidence':[{'operation_id':'op','changes':[]}]}
        self.assertFalse(self.module.continuity_proof([row],[installed,started,write])['passed'])
        for path in ('README.md','tests/test_new.py','unused.py'):
            changed={**row,'mutation_evidence':[{'operation_id':'op','changes':[{'path':path,
                     'before_sha256':'c'*64,'after_sha256':'d'*64,'before_ast_sha256':'e'*64,'after_ast_sha256':'f'*64}]}]}
            self.assertFalse(self.module.continuity_proof([changed],[installed,started,write])['passed'])

    def test_implementation_snapshot_ignores_format_docs_and_tests_but_observes_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'impl.py').write_text('def value():\n return 1\n')
            (root/'test_tmp.py').write_text('sentinel=1\n');(root/'README.md').write_text('one')
            before=self.module.implementation_versions(root,())
            (root/'impl.py').write_text('# comment\ndef value():\n    """different docs"""\n    return 1\n')
            same=self.module.implementation_versions(root,())
            self.assertEqual(set(before),{'impl.py'})
            self.assertEqual(before['impl.py']['ast_sha256'],same['impl.py']['ast_sha256'])
            (root/'impl.py').write_text('def value():\n return 2\n')
            changed=self.module.implementation_versions(root,())
            self.assertNotEqual(before['impl.py']['ast_sha256'],changed['impl.py']['ast_sha256'])

    def test_actual_tool_change_and_noop_have_distinct_note_bound_proofs(self):
        from agent_lab.store import Store
        from scripts.deepseek_live import RecordingModel
        from tests.support import ScriptedModel,tool,final
        from tests.dev.test_compaction import summary
        for changed in (False,True):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory(prefix='mission-mutation-') as temp:
                base=Path(temp);root=base/'project';root.mkdir()
                (root/'impl.py').write_text('def value():\n return 1\n')
                config={'workspace':str(root),'db':str(base/'tasks.sqlite'),'task':'Read actual code.',
                        'action':'start','read_only_paths':[],'limits':{'max_steps':8,'max_seconds':60,
                        'max_context_bytes':64000},'receipt_dir':str(base/'first/receipts'),'allow_api':False}
                first=self.module.execute_turn(config,model=RecordingModel(ScriptedModel([
                    tool('read_file',{'path':'impl.py'}),final('Earlier work notes. '*350)]),config['receipt_dir']))
                config.update(action='continue',task_id=first['id'],task='Complete the new change.',
                              receipt_dir=str(base/'second/receipts'),
                              limits={**config['limits'],'auto_compact_bytes':1,'max_auto_compactions':1})
                value=2 if changed else 1
                second=self.module.execute_turn(config,model=RecordingModel(ScriptedModel([summary(),
                    tool('write_file',{'path':'impl.py','content':'def value():\n return '+str(value)+'\n'}),final()]),config['receipt_dir']))
                self.assertEqual(second['phase'],'completed')
                observed=self.module.observe_stdin(root,'impl','value',[[]],10)
                self.assertEqual(observed['observations'],[value])
                row={**second,'model_visible_summaries':self.module.request_summary_evidence(base/'second'),
                     'mutation_evidence':self.module.read_mutation_evidence(base/'second'),
                     'executed_python_paths':observed['executed_python_paths']}
                with Store(base/'tasks.sqlite') as store:events=store.events(first['id'])
                self.assertEqual(self.module.continuity_proof([row],events)['passed'],changed,
                    json.dumps({'proof':self.module.continuity_proof([row],events),
                    'mutation':row['mutation_evidence'],'paths':observed['executed_python_paths'],
                    'notes':row['model_visible_summaries'],
                    'events':[e for e in events if e['kind'] in ('model_started','compaction_installed','tool_finished')]}))

    def test_mutation_snapshot_reads_multiple_implementation_files_in_same_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('alpha.py','beta.py'):(root/name).write_text('def value(): return 1\n')
            (root/'README.md').write_text('not implementation')
            versions=self.module.implementation_versions(root,())
            self.assertEqual(set(versions),{'alpha.py','beta.py'})
            self.assertTrue(all(v['ast_sha256'] is not None for v in versions.values()))
