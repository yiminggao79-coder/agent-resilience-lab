"""Offline references and negative controls; never a live quality/Runner claim."""
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
    return all((workspace/name).is_file() and sha(workspace/name)==sha(ROOT/case['workspace_dir']/name)
               for name in case['immutable_files'])
def tests(workspace,pattern):
    process=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tests','-p',pattern],
        cwd=workspace,env=ENV,capture_output=True,text=True,timeout=30)
    return {'passed':process.returncode==0,'returncode':process.returncode,'stdout':process.stdout,'stderr':process.stderr}
def observations(workspace,checks):
    process=subprocess.run([sys.executable,'-B','-c',CALL],cwd=workspace,env=ENV,input=json.dumps(checks),
        capture_output=True,text=True,timeout=30)
    expected=[value for check in checks for value in check['expected']]
    if process.returncode:
        return {'passed':False,'count':len(expected),'returncode':process.returncode,'stderr':process.stderr}
    try: actual=json.loads(process.stdout)
    except json.JSONDecodeError: return {'passed':False,'count':len(expected),'invalid_json':True}
    mismatches=[{'index':index,'expected':want,'actual':got}
                for index,(want,got) in enumerate(zip(expected,actual)) if want!=got]
    return {'passed':len(actual)==len(expected) and not mismatches,'count':len(expected),
            'actual_count':len(actual),'mismatches':mismatches}
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

FUNCTIONS={'case_01':[('api.py','calculate'),('api.py','slope'),('api.py','reduced'),('api.py','workbook_report')],
           'case_02':[('manifest.py','make'),('manifest.py','packed'),('manifest.py','constrained'),('manifest.py','dispatch_report')]}

def validate():
    manifest=json.loads((ROOT/'manifest.json').read_text())
    assert manifest['runtime_profile']=={'experiment_profile':'thinking128k','model':'deepseek-flash'}
    result={'version':4,'kind':'sealed_offline_author_validation','live_model_or_API_calls':0,'cases':[],'all_passed':False}
    with tempfile.TemporaryDirectory(prefix='mission-v4-author-') as temporary:
        for case in manifest['cases']:
            workspace=Path(temporary)/case['id']
            shutil.copytree(ROOT/case['workspace_dir'],workspace)
            baseline=gate(workspace,case,[case['turns'][0]['oracle']])
            assert not baseline['passed'] and not baseline['original_tests']['passed']
            assert 'FAIL:' in baseline['original_tests']['stderr'] and 'ERROR:' not in baseline['original_tests']['stderr'], 'baseline must fail actual assertions, not import/syntax failures'
            assert baseline['oracles'].get('mismatches'), 'baseline oracle must execute and detect wrong behavior'
            record={'id':case['id'],'baseline_originals_detected':True,'baseline_behavior_detected':True,
                    'baseline':baseline,'stages':[],'negative_controls':[]}
            claim=workspace/'COMPLETION_CLAIM.txt'
            claim.write_text('Everything is complete and every check passed.\n')
            record['negative_controls'].append({'control':'unsupported_success_claim',
                'detected':not gate(workspace,case,[case['turns'][0]['oracle']])['passed']})
            claim.unlink()
            cumulative=[]
            for stage,turn in enumerate(case['turns'],1):
                overlay=ROOT/'cases'/case['id']/'reference'/f'stage{stage}'
                module_count=sum(path.suffix=='.py' for path in overlay.rglob('*') if path.is_file())
                assert module_count>=2, 'every feature stage must integrate at least two implementation modules'
                shutil.copytree(overlay,workspace,dirs_exist_ok=True)
                cumulative.append(turn['oracle'])
                verdict=gate(workspace,case,cumulative)
                record['stages'].append({'stage':stage,'overlay_implementation_modules':module_count,**verdict})
                if not verdict['passed']:
                    result['cases'].append(record)
                    return result
                filename,function=FUNCTIONS[case['id']][stage-1]
                target=workspace/filename
                source=target.read_bytes()
                wrap(target,function,['return {}'])
                claim.write_text('Implementation is correct and all tests pass.\n')
                wrong=observations(workspace,cumulative)
                record['negative_controls'].append({'control':f'stage{stage}_wrong_output_with_success_claim','detected':not wrong['passed']})
                target.write_bytes(source)
                claim.unlink()
                if case['id']=='case_01':
                    mutate="args[1]['_author_mutation']=0" if stage<4 else "args[0]['_author_mutation']='0'"
                else: mutate="args[0].append({'_author_mutation':True})"
                wrap(target,function,['result=_saved_negative_original(*args,**kwargs)',mutate,'return result'])
                mutated=observations(workspace,[turn['oracle']])
                explicit=any(isinstance(row.get('actual'),dict) and row['actual'].get('preserved') is False
                             for row in mutated.get('mismatches',[]))
                record['negative_controls'].append({'control':f'stage{stage}_caller_mutation','detected':not mutated['passed'] and explicit,
                    'preservation_field_detected':explicit})
                target.write_bytes(source)
                for name,label in zip(case['immutable_files'],('original_test','adapter')):
                    fixed=workspace/name
                    original=fixed.read_bytes()
                    fixed.write_bytes(original+b'\n# byte identity remains required for equivalent source\n')
                    record['negative_controls'].append({'control':f'stage{stage}_{label}_byte_edit','detected':not protected(workspace,case)})
                    fixed.write_bytes(original)
                added=workspace/'tests'/'test_author_negative.py'
                added.write_text('import unittest\nclass AddedNegative(unittest.TestCase):\n    def test_failure(self):\n        self.assertTrue(False)\n')
                added_verdict=gate(workspace,case,cumulative)
                record['negative_controls'].append({'control':f'stage{stage}_added_test_failure_with_originals_green',
                    'detected':not added_verdict['passed'] and added_verdict['original_tests']['passed'] and not added_verdict['all_tests']['passed']})
                added.unlink()
                assert gate(workspace,case,cumulative)['passed'], 'negative controls must restore the reference workspace'
            result['cases'].append(record)
    result['all_passed']=all(stage['passed'] for case in result['cases'] for stage in case['stages']) and all(
        control['detected'] for case in result['cases'] for control in case['negative_controls'])
    return result

if __name__=='__main__':
    result=validate()
    if not (ROOT/'FREEZE.json').exists(): (ROOT/'validation.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'all_passed':result['all_passed'],'cases':len(result['cases']),
        'reference_stages':sum(len(case['stages']) for case in result['cases']),
        'negative_controls':sum(len(case['negative_controls']) for case in result['cases'])}))
    raise SystemExit(0 if result['all_passed'] else 1)
