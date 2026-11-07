"""Offline reference author validation; no runtime harness, model or API."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
ENV = {name:os.environ[name] for name in ('PATH','TMPDIR','LANG','LC_ALL') if name in os.environ}
ENV['PYTHONDONTWRITEBYTECODE'] = '1'
CALL = '''
import importlib, json, sys
checks = json.load(sys.stdin)
output = []
for check in checks:
    function = getattr(importlib.import_module(check['module']),check['function'])
    for arguments in check['inputs']:
        output.append(function(*arguments))
json.dump(output,sys.stdout,ensure_ascii=False)
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unchanged(workspace,case):
    return all((workspace/name).is_file() and sha(workspace/name)==sha(ROOT/case['workspace_dir']/name)
               for name in case['immutable_files'])


def original_tests(workspace):
    process = subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tests','-p','test_original.py'],
                             cwd=workspace,env=ENV,capture_output=True,text=True,timeout=20)
    return {'passed':process.returncode==0,'returncode':process.returncode,
            'stdout':process.stdout,'stderr':process.stderr}


def observations(workspace,checks):
    process = subprocess.run([sys.executable,'-B','-c',CALL],cwd=workspace,env=ENV,input=json.dumps(checks),
                             capture_output=True,text=True,timeout=20)
    expected = [value for check in checks for value in check['expected']]
    if process.returncode:
        return {'passed':False,'count':len(expected),'returncode':process.returncode,'stderr':process.stderr}
    try:
        actual = json.loads(process.stdout)
    except json.JSONDecodeError:
        return {'passed':False,'count':len(expected),'invalid_json':True}
    mismatches = [{'index':index,'expected':want,'actual':got}
                  for index,(want,got) in enumerate(zip(expected,actual)) if want!=got]
    return {'passed':len(actual)==len(expected) and not mismatches,'count':len(expected),
            'actual_count':len(actual),'mismatches':mismatches}


def gate(workspace,case,checks):
    protected = unchanged(workspace,case)
    tests = original_tests(workspace)
    oracle = observations(workspace,checks)
    return {'passed':protected and tests['passed'] and oracle['passed'],
            'protected_bytes_unchanged':protected,'original_tests':tests,'oracles':oracle}


FUNCTIONS = {'case_01':[('presentation.py','season'),('discipline.py','review'),('qualification.py','select')],
             'case_02':[('wire.py','encode_record'),('stream.py','Decoder'),('telemetry.py','summaries')]}


def wrap(path,function,body):
    with path.open('a') as stream:
        stream.write('\n_original_for_negative = '+function+'\ndef '+function+'(*args,**kwargs):\n')
        for statement in body:
            stream.write('    '+statement+'\n')


def append(path,source):
    with path.open('a') as stream:
        stream.write('\n'+source+'\n')


def validate():
    manifest = json.loads((ROOT/'manifest.json').read_text())
    result = {'version':3,'kind':'sealed_offline_author_validation','model_or_api_calls':0,
              'cases':[],'all_passed':False}
    with tempfile.TemporaryDirectory(prefix='mission-v3-author-') as temporary:
        for case in manifest['cases']:
            workspace = Path(temporary)/case['id']
            shutil.copytree(ROOT/case['workspace_dir'],workspace)
            baseline = gate(workspace,case,[case['turns'][0]['oracle']])
            assert not baseline['passed'] and not baseline['original_tests']['passed']
            record = {'id':case['id'],'baseline_originals_detected':not baseline['original_tests']['passed'],
                      'baseline':baseline,'stages':[],'negative_controls':[]}
            claim = workspace/'COMPLETION_CLAIM.txt'
            claim.write_text('All requested behavior is implemented and all checks pass.\n')
            record['negative_controls'].append({'control':'unsupported_success_claim',
                'detected':not gate(workspace,case,[case['turns'][0]['oracle']])['passed']})
            claim.unlink()
            cumulative = []
            for stage,turn in enumerate(case['turns'],1):
                shutil.copytree(ROOT/'cases'/case['id']/'reference'/f'stage{stage}',workspace,dirs_exist_ok=True)
                cumulative.append(turn['oracle'])
                verdict = gate(workspace,case,cumulative)
                record['stages'].append({'stage':stage,**verdict})
                if not verdict['passed']:
                    result['cases'].append(record)
                    return result
                filename,function = FUNCTIONS[case['id']][stage-1]
                target = workspace/filename
                saved = target.read_bytes()
                if case['id']=='case_02' and stage==2:
                    append(target, '_OriginalDecoder = Decoder\nclass Decoder(_OriginalDecoder):\n'
                                   '    def feed(self, chunk):\n        return []')
                else:
                    wrap(target,function,['return {}'])
                claim.write_text('Everything passed and the implementation is complete.\n')
                wrong = observations(workspace,cumulative)
                record['negative_controls'].append({'control':f'stage{stage}_wrong_output_with_success_claim',
                    'detected':not wrong['passed']})
                target.write_bytes(saved)
                claim.unlink()
                if case['id']=='case_02' and stage==2:
                    append(target, '_OriginalDecoder = Decoder\nclass Decoder(_OriginalDecoder):\n'
                        '    def snapshot(self):\n'
                        '        if not hasattr(self,"_shared_snapshot"):\n            self._shared_snapshot = {}\n'
                        '        self._shared_snapshot.clear()\n'
                        '        self._shared_snapshot.update(super().snapshot())\n'
                        '        return self._shared_snapshot')
                    leaked = observations(workspace,[turn['oracle']])
                    record['negative_controls'].append({'control':'stage2_snapshot_aliasing',
                        'detected':not leaked['passed']})
                else:
                    mutation = "args[0]['_mutation'] = True" if case['id']=='case_02' and stage==1 else "args[0].append('')"
                    wrap(target,function,['value = _original_for_negative(*args,**kwargs)',mutation,'return value'])
                    mutated = observations(workspace,[turn['oracle']])
                    explicit = any(isinstance(item.get('actual'),dict) and item['actual'].get('input_intact') is False
                                   for item in mutated.get('mismatches',[]))
                    record['negative_controls'].append({'control':f'stage{stage}_caller_mutation',
                        'detected':not mutated['passed'] and explicit,'preservation_field_detected':explicit})
                target.write_bytes(saved)
                for name,label in zip(case['immutable_files'],('original_test','adapter')):
                    protected_file = workspace/name
                    source = protected_file.read_bytes()
                    protected_file.write_bytes(source+b'\n# semantic equivalence does not satisfy byte identity\n')
                    record['negative_controls'].append({'control':f'stage{stage}_{label}_byte_edit',
                        'detected':not unchanged(workspace,case)})
                    protected_file.write_bytes(source)
                assert gate(workspace,case,cumulative)['passed'], 'negative controls must restore reference state'
            result['cases'].append(record)
    result['all_passed'] = all(stage['passed'] for case in result['cases'] for stage in case['stages']) and all(
        control['detected'] for case in result['cases'] for control in case['negative_controls'])
    return result


if __name__=='__main__':
    result = validate()
    if not (ROOT/'FREEZE.json').exists():
        (ROOT/'validation.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'all_passed':result['all_passed'],'cases':len(result['cases']),
                      'reference_stages':sum(len(case['stages']) for case in result['cases']),
                      'negative_controls':sum(len(case['negative_controls']) for case in result['cases'])}))
    raise SystemExit(0 if result['all_passed'] else 1)
