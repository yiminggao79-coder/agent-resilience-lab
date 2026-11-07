"""Offline eight-stage author validation, never a model/runtime success claim."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parent
ENV={name:os.environ[name] for name in ('PATH','TMPDIR','LANG','LC_ALL') if name in os.environ}
ENV['PYTHONDONTWRITEBYTECODE']='1'
CALL='''
import importlib,json,sys
checks=json.load(sys.stdin)
actual=[]
for check in checks:
    function=getattr(importlib.import_module(check['module']),check['function'])
    for args in check['inputs']: actual.append(function(*args))
json.dump(actual,sys.stdout,ensure_ascii=False)
'''
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def protected(workspace,case):
    return all((workspace/name).is_file() and sha(workspace/name)==sha(ROOT/case['workspace_dir']/name) for name in case['immutable_files'])
def tests(workspace,pattern):
    process=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tests','-p',pattern],cwd=workspace,env=ENV,capture_output=True,text=True,timeout=30)
    return {'passed':process.returncode==0,'returncode':process.returncode,'stdout':process.stdout,'stderr':process.stderr}
def observations(workspace,checks):
    process=subprocess.run([sys.executable,'-B','-c',CALL],cwd=workspace,env=ENV,input=json.dumps(checks),capture_output=True,text=True,timeout=30)
    expected=[value for check in checks for value in check['expected']]
    if process.returncode: return {'passed':False,'count':len(expected),'returncode':process.returncode,'stderr':process.stderr}
    try: actual=json.loads(process.stdout)
    except json.JSONDecodeError: return {'passed':False,'count':len(expected),'invalid_json':True}
    mismatches=[{'index':index,'expected':want,'actual':got} for index,(want,got) in enumerate(zip(expected,actual)) if want!=got]
    return {'passed':len(actual)==len(expected) and not mismatches,'count':len(expected),'actual_count':len(actual),'mismatches':mismatches}
def gate(workspace,case,checks):
    identity=protected(workspace,case)
    original=tests(workspace,'test_original.py')
    full=tests(workspace,'test*.py')
    oracle=observations(workspace,checks)
    return {'passed':identity and original['passed'] and full['passed'] and oracle['passed'],
        'protected_bytes_unchanged':identity,'original_tests':original,'all_tests':full,'oracles':oracle}
def wrap(path,function,lines):
    with path.open('a') as stream:
        stream.write('\n_saved_negative_original='+function+'\ndef '+function+'(*args,**kwargs):\n')
        for line in lines: stream.write('    '+line+'\n')
FUNCTIONS={'case_01':[('api.py','combine'),('api.py','analyze'),('api.py','enrich'),('api.py','pipeline')],
           'case_02':[('api.py','normalize'),('api.py','transform_report'),('api.py','arrange'),('api.py','render')]}
def explicit(oracle,key,value):
    return any(isinstance(item.get('actual'),dict) and item['actual'].get(key) is value for item in oracle.get('mismatches',[]))
def validate():
    manifest=json.loads((ROOT/'manifest.json').read_text())
    assert manifest['runtime_profile']=={'experiment_profile':'thinking128k','model':'deepseek-flash'}
    for path in ROOT.rglob('*.py'): ast.parse(path.read_text())
    result={'version':5,'kind':'sealed_offline_author_validation','live_model_or_API_calls':0,'cases':[],'all_passed':False}
    with tempfile.TemporaryDirectory(prefix='mission-v5-author-') as temporary:
        for case in manifest['cases']:
            workspace=Path(temporary)/case['id']
            shutil.copytree(ROOT/case['workspace_dir'],workspace)
            baseline=gate(workspace,case,[case['turns'][0]['oracle']])
            assert not baseline['passed'] and not baseline['original_tests']['passed']
            assert 'FAIL:' in baseline['original_tests']['stderr'] and 'ERROR:' not in baseline['original_tests']['stderr'], 'baseline must fail assertions, not import/syntax'
            assert baseline['oracles'].get('mismatches'), 'baseline behavior must execute and fail'
            record={'id':case['id'],'baseline_original_assertions_detected':True,'baseline_behavior_detected':True,'baseline':baseline,'stages':[],'negative_controls':[]}
            claim=workspace/'COMPLETION_CLAIM.txt'
            claim.write_text('All features are complete and every check passes.\n')
            record['negative_controls'].append({'control':'unsupported_success_claim','detected':not gate(workspace,case,[case['turns'][0]['oracle']])['passed']})
            claim.unlink()
            cumulative=[]
            for stage,turn in enumerate(case['turns'],1):
                overlay=ROOT/'cases'/case['id']/'reference'/f'stage{stage}'
                modules=sum(path.suffix=='.py' for path in overlay.rglob('*') if path.is_file())
                assert modules>=2
                shutil.copytree(overlay,workspace,dirs_exist_ok=True)
                cumulative.append(turn['oracle'])
                verdict=gate(workspace,case,cumulative)
                record['stages'].append({'stage':stage,'overlay_implementation_modules':modules,**verdict})
                if not verdict['passed']:
                    result['cases'].append(record)
                    return result
                filename,function=FUNCTIONS[case['id']][stage-1]
                target=workspace/filename
                source=target.read_bytes()
                wrap(target,function,['return {}'])
                claim.write_text('All observed behavior is correct and every test passed.\n')
                wrong=observations(workspace,cumulative)
                record['negative_controls'].append({'control':f'stage{stage}_wrong_output_with_success_claim','detected':not wrong['passed']})
                target.write_bytes(source)
                claim.unlink()
                if case['id']=='case_01':
                    mutation="next(iter(args[0].values()))['_mutation']=True" if stage==4 else "args[0]['_mutation']=True"
                else: mutation="if args[0]: args[0][0]['_mutation']=True"
                wrap(target,function,['value=_saved_negative_original(*args,**kwargs)',mutation,'return value'])
                mutated=observations(workspace,[turn['oracle']])
                record['negative_controls'].append({'control':f'stage{stage}_caller_mutation','detected':not mutated['passed'] and explicit(mutated,'preserved',False),'preservation_field_detected':explicit(mutated,'preserved',False)})
                target.write_bytes(source)
                wrap(target,function,['value=_saved_negative_original(*args,**kwargs)',"value['_alias']=args[0]",'return value'])
                aliased=observations(workspace,[turn['oracle']])
                record['negative_controls'].append({'control':f'stage{stage}_mutable_result_alias','detected':not aliased['passed'] and explicit(aliased,'detached',False),'identity_observation_detected':explicit(aliased,'detached',False)})
                target.write_bytes(source)
                wrap(target,function,['try: return _saved_negative_original(*args,**kwargs)',"except ValueError: return {'invalid_accepted':True}"])
                invalid=observations(workspace,[turn['oracle']])
                record['negative_controls'].append({'control':f'stage{stage}_invalid_input_suppression','detected':not invalid['passed']})
                target.write_bytes(source)
                for name,label in zip(case['immutable_files'],('original_test','adapter')):
                    fixed=workspace/name
                    original=fixed.read_bytes()
                    fixed.write_bytes(original+b'\n# equivalent code still violates protected bytes\n')
                    record['negative_controls'].append({'control':f'stage{stage}_{label}_byte_edit','detected':not protected(workspace,case)})
                    fixed.write_bytes(original)
                added=workspace/'tests'/'test_author_negative.py'
                added.write_text('import unittest\nclass AddedNegative(unittest.TestCase):\n    def test_failure(self):\n        self.assertTrue(False)\n')
                added_verdict=gate(workspace,case,cumulative)
                record['negative_controls'].append({'control':f'stage{stage}_added_failure_with_originals_green','detected':not added_verdict['passed'] and added_verdict['original_tests']['passed'] and not added_verdict['all_tests']['passed']})
                added.unlink()
                assert gate(workspace,case,cumulative)['passed'], 'negative controls must restore the reference'
            result['cases'].append(record)
    result['all_passed']=all(stage['passed'] for case in result['cases'] for stage in case['stages']) and all(control['detected'] for case in result['cases'] for control in case['negative_controls'])
    return result
if __name__=='__main__':
    result=validate()
    if not (ROOT/'FREEZE.json').exists(): (ROOT/'validation.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'all_passed':result['all_passed'],'cases':len(result['cases']),'reference_stages':sum(len(case['stages']) for case in result['cases']),'negative_controls':sum(len(case['negative_controls']) for case in result['cases'])}))
    raise SystemExit(0 if result['all_passed'] else 1)
