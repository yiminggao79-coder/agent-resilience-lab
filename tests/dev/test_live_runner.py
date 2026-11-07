import tempfile
from pathlib import Path
import unittest

try:
    from scripts.deepseek_live import make_case, check_case
except ImportError:
    make_case = None


class LiveOracleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_result_acceptance_ignores_model_claim_and_checks_function_values(self):
        self.assertIsNotNone(make_case, 'live case runner missing')
        case = make_case(self.root, 'add')
        # baseline is wrong although a model may announce completion
        bad = check_case(self.root, case, observer=lambda *_: {'complete': True, 'observations': [-1, -11, 0, -1.0], 'exit_code': 0})
        self.assertFalse(bad['oracle_matches'])
        # oracle compares literal expectations outside candidate-controlled code
        good = check_case(self.root, case, observer=lambda *_: {'complete': True, 'observations': [5, 5, 0, 4.0], 'exit_code': 0})
        self.assertTrue(good['oracle_matches'])
        self.assertTrue(good['public_tests_unchanged'])
        (self.root/'tests'/'test_public.py').write_text('')
        changed = check_case(self.root, case, observer=lambda *_: {'complete': True, 'observations': [5, 5, 0, 4.0], 'exit_code': 0})
        self.assertFalse(changed['public_tests_unchanged'])

    def test_incomplete_and_short_observation_cannot_pass(self):
        self.assertIsNotNone(make_case, 'live case runner missing')
        case = make_case(self.root, 'add')
        for response in ({'complete': False, 'observations': [], 'exit_code': 0},
                         {'complete': True, 'observations': [5], 'exit_code': 0},
                         {'complete': True, 'observations': [5, 5, 0, True], 'exit_code': 0}):
            self.assertFalse(check_case(self.root, case, observer=lambda *_, result=response: result)['oracle_matches'])

    def test_coding_cycle_requires_successful_tests_after_last_edit(self):
        from scripts.deepseek_live import coding_cycle
        import json
        def step(name, result):
            return [{'role':'assistant','tool_calls':[{'id':name,'function':{'name':name}}]},
                    {'role':'tool','tool_call_id':name,'content':json.dumps({'ok':True,**result})}]
        before = step('read_file', {}) + step('run_tests', {'exit_code':0}) + step('apply_patch', {})
        self.assertFalse(coding_cycle(before))
        self.assertFalse(coding_cycle(before + step('run_tests', {'exit_code':1})))
        self.assertTrue(coding_cycle(before + step('run_tests', {'exit_code':0})))
        self.assertFalse(coding_cycle(before + step('run_tests', {'exit_code':0}) + step('write_file', {})))

    def test_model_receipts_preserve_success_before_later_failure_without_raw_error(self):
        from scripts.deepseek_live import RecordingModel
        from agent_lab.model import ModelError
        from tests.support import ScriptedModel,final
        delegate=ScriptedModel([final('private model text'),ModelError('http_error',http_status=500,diagnostic=['model','PRIVATE_ERROR'],response_body_bytes=12)])
        recorder=RecordingModel(delegate,self.root)
        recorder.complete([{'role':'user','content':'task'}],[],1,32)
        with self.assertRaises(ModelError):
            recorder.complete([],[],1,32)
        self.assertEqual(recorder.receipts[0]['usage'],{'input_tokens':100,'output_tokens':20})
        self.assertEqual(recorder.receipts[1]['http_status'],500)
        self.assertEqual(recorder.receipts[1]['diagnostic'],['model'])
        self.assertEqual(recorder.receipts[1]['response_body_bytes'],12)
        self.assertNotIn('PRIVATE_ERROR',str(recorder.receipts))
        self.assertNotIn('private model text',str(recorder.receipts))
        self.assertTrue((self.root/'model-001-response.json').exists())
        self.assertTrue((self.root/'model-002-error.json').exists())

    def test_possible_command_mutation_after_verification_invalidates_cycle(self):
        from scripts.deepseek_live import coding_cycle
        import json
        def step(name,result):
            return [{'role':'assistant','tool_calls':[{'id':name,'function':{'name':name}}]},
                    {'role':'tool','tool_call_id':name,'content':json.dumps(result)}]
        verified=step('read_file',{'ok':True})+step('write_file',{'ok':True})+step('run_tests',{'ok':True,'exit_code':0})
        for result in ({'ok':True,'exit_code':0},{'ok':False,'exit_code':1}):
            self.assertFalse(coding_cycle(verified+step('run_command',result)))
        self.assertTrue(coding_cycle(verified+step('run_command',{'ok':True,'exit_code':0})+step('run_tests',{'ok':True,'exit_code':0})))

    def test_patch_text_requires_verification_after_its_last_potential_effect(self):
        from scripts.deepseek_live import coding_cycle
        import json
        def step(name,result):
            return [{'role':'assistant','tool_calls':[{'id':name,'function':{'name':name}}]},
                    {'role':'tool','tool_call_id':name,'content':json.dumps(result)}]
        patch=step('read_file',{'ok':True})+step('apply_patch_text',{'ok':True,'steps':3})
        self.assertTrue(coding_cycle(patch+step('run_tests',{'ok':True,'exit_code':0})))
        self.assertFalse(coding_cycle(patch+step('run_tests',{'ok':True,'exit_code':0})+step('apply_patch_text',{'ok':False,'error':'patch_conflict'})))

    def test_large_edit_after_test_invalidates_prior_verification(self):
        from scripts.deepseek_live import coding_cycle
        import json
        def step(name,result):
            return [{'role':'assistant','tool_calls':[{'id':name,'function':{'name':name}}]},
                    {'role':'tool','tool_call_id':name,'content':json.dumps(result)}]
        verified=step('read_file',{'ok':True})+step('write_file',{'ok':True})+step('run_tests',{'ok':True,'exit_code':0})
        self.assertFalse(coding_cycle(verified+step('edit_file',{'ok':True})))
        self.assertFalse(coding_cycle(verified+step('edit_file',{'ok':False,'error':'worker_failed'})))
        self.assertTrue(coding_cycle(verified+step('edit_file',{'ok':True})+step('run_tests',{'ok':True,'exit_code':0})))
        self.assertTrue(coding_cycle(step('read_lines',{'ok':True})+step('edit_file',{'ok':True})+step('run_tests',{'ok':True,'exit_code':0})))

    def test_latest_failed_or_malformed_test_result_invalidates_earlier_success(self):
        from scripts.deepseek_live import coding_cycle
        import json
        def step(name,result):
            return [{'role':'assistant','tool_calls':[{'id':name,'function':{'name':name}}]},
                    {'role':'tool','tool_call_id':name,'content':json.dumps(result)}]
        verified=step('read_file',{'ok':True})+step('write_file',{'ok':True})+step('run_tests',{'ok':True,'exit_code':0})
        for result in ({'ok':True,'exit_code':1},{'ok':False,'error':'tool_timeout'}):
            with self.subTest(result=result):
                self.assertFalse(coding_cycle(verified+step('run_tests',result)))
        malformed=step('run_tests',{'ok':True})
        malformed[-1]['content']='unparseable'
        self.assertFalse(coding_cycle(verified+malformed))
        self.assertTrue(coding_cycle(verified+malformed+step('run_tests',{'ok':True,'exit_code':0})))

    def test_stream_probe_receipts_contain_only_counts_hash_and_timings(self):
        from scripts.deepseek_live import StreamMetadata,RecordingModel
        from tests.support import final
        class StreamFake:
            identity={'fixture':'stream'}
            def complete(self,*args):
                observer.on_text('private ')
                observer.on_text('text')
                return final('private text')
        observer=StreamMetadata()
        recorder=RecordingModel(StreamFake(),self.root,text_observer=observer)
        recorder.complete([],[],1,64)
        report=recorder.receipts[0]['text_stream']
        self.assertEqual(report['chunks'],2)
        self.assertEqual(report['utf8_bytes'],12)
        self.assertTrue(report['matches_completed_content'])
        self.assertTrue(report['first_delta_before_return'])
        self.assertNotIn('private text',str(recorder.receipts))
