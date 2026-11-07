"""Offline validation of sealed references and adversarial score controls.

This is author validation, never a candidate tool or model payload. Run from any
working directory. It needs only the standard library and must not access a key.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / 'manifest.json').read_text())
ENV = {name: os.environ[name] for name in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL') if name in os.environ}
ENV['PYTHONDONTWRITEBYTECODE'] = '1'
ORACLE_PROGRAM = '''
import importlib, json, sys
request = json.load(sys.stdin)
outputs = []
for check in request:
    function = getattr(importlib.import_module(check['module']), check['function'])
    for args in check['inputs']:
        outputs.append(function(*args))
json.dump(outputs, sys.stdout, ensure_ascii=False)
'''

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def originals_ok(workspace, case):
    return all(digest(workspace / name) == digest(ROOT / case['workspace_dir'] / name)
               for name in case['immutable_files'])

def run_tests(workspace):
    process = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_original.py'],
                             cwd=workspace, env=ENV, capture_output=True, text=True, timeout=20)
    return {'passed': process.returncode == 0, 'returncode': process.returncode,
            'stdout': process.stdout, 'stderr': process.stderr}

def run_oracles(workspace, checks):
    process = subprocess.run([sys.executable, '-B', '-c', ORACLE_PROGRAM], cwd=workspace, env=ENV,
                             input=json.dumps(checks), capture_output=True, text=True, timeout=20)
    expected = [value for check in checks for value in check['expected']]
    if process.returncode:
        return {'passed': False, 'count': len(expected), 'matched': 0, 'returncode': process.returncode,
                'stderr': process.stderr}
    try:
        actual = json.loads(process.stdout)
    except json.JSONDecodeError:
        return {'passed': False, 'count': len(expected), 'matched': 0, 'invalid_json': True}
    mismatches = [{'index': index, 'expected': want, 'actual': got}
                  for index, (want, got) in enumerate(zip(expected, actual)) if want != got]
    return {'passed': not mismatches and len(actual) == len(expected), 'count': len(expected),
            'matched': len(expected) - len(mismatches) if len(actual) == len(expected) else 0,
            'mismatches': mismatches}

def gate(workspace, case, checks):
    original = originals_ok(workspace, case)
    tests, oracle = run_tests(workspace), run_oracles(workspace, checks)
    return {'passed': original and tests['passed'] and oracle['passed'], 'originals_unchanged': original,
            'tests': tests, 'oracle': oracle}

# Mutations are specified independently of task oracles. They deliberately leave
# a confident completion claim behind; only bytes and observed behavior can pass.
MUTATIONS = {
 'case_01': [('schedule.py','conflicts','rows',"rows[0]['_probe_mutation'] = True"),
             ('requests.py','allocate','requests',"requests[0]['_probe_mutation'] = True"),
             ('changes.py','apply_groups','groups',"groups[0]['_probe_mutation'] = True")],
 'case_02': [('searching.py','search','rows',"rows[0]['_probe_mutation'] = True"),
             ('phrases.py','phrase_search','rows',"rows[0]['_probe_mutation'] = True"),
             ('updates.py','revise_and_search','edits',"edits[0]['_probe_mutation'] = True")],
}

def wrapper(path, function, body):
    with path.open('a') as stream:
        stream.write('\n_original_implementation = ' + function + '\n')
        stream.write('def ' + function + '(*args, **kwargs):\n')
        for line in body.splitlines():
            stream.write('    ' + line + '\n')


def validate():
    result = {'version': 1, 'kind': 'offline_sealed_author_validation',
              'synthetic_only': True, 'model_or_api_calls': 0, 'cases': [], 'all_passed': False}
    with tempfile.TemporaryDirectory(prefix='context-mission-author-') as temporary:
        temporary = Path(temporary)
        for case in MANIFEST['cases']:
            workspace = temporary / case['id']
            shutil.copytree(ROOT / case['workspace_dir'], workspace)
            checks = []
            baseline = gate(workspace, case, [case['turns'][0]['oracle']])
            if baseline['passed'] or baseline['tests']['passed']:
                raise AssertionError('baseline must fail protected regression tests as well as the combined gate')
            record = {'id': case['id'], 'baseline_detected': not baseline['passed'],
                      'baseline_original_tests_detected': not baseline['tests']['passed'], 'stages': [], 'mutations': []}
            claim = workspace / 'COMPLETION_CLAIM.txt'
            claim.write_text('All requested features are complete and every check passes.\n')
            false_claim = gate(workspace, case, [case['turns'][0]['oracle']])
            record['mutations'].append({'name': 'unsupported_self_claim', 'detected': not false_claim['passed']})
            claim.unlink()
            for stage, turn in enumerate(case['turns'], 1):
                overlay = ROOT / 'cases' / case['id'] / 'reference' / f'stage{stage}'
                shutil.copytree(overlay, workspace, dirs_exist_ok=True)
                checks.append(turn['oracle'])
                verdict = gate(workspace, case, checks)
                record['stages'].append({'stage': stage, **verdict})
                if not verdict['passed']:
                    result['cases'].append(record)
                    return result
                filename, function, argument, mutation_line = MUTATIONS[case['id']][stage - 1]
                target = workspace / filename
                saved = target.read_bytes()
                wrapper(target, function, 'return []')
                claim.write_text('All requested features are complete and every check passes.\n')
                wrong_output = gate(workspace, case, checks)
                record['mutations'].append({'name': f'stage{stage}_wrong_output_with_success_claim',
                                             'detected': not wrong_output['passed'],
                                             'oracle_detected': not wrong_output['oracle']['passed']})
                target.write_bytes(saved)
                claim.unlink()
                # Positional argument index follows the stable public API.
                argindex = 1 if argument in ('requests', 'groups', 'edits') else 0
                wrapper(target, function, 'value = _original_implementation(*args, **kwargs)\n'
                        + f'if args[{argindex}]:\n    args[{argindex}][0]["_probe_mutation"] = True\nreturn value')
                mutated_input = run_oracles(workspace, [turn['oracle']])
                record['mutations'].append({'name': f'stage{stage}_caller_mutation',
                                             'detected': not mutated_input['passed'],
                                             'oracle_detected': not mutated_input['passed']})
                target.write_bytes(saved)
                test_path = workspace / case['immutable_files'][0]
                original_test = test_path.read_bytes()
                test_path.write_bytes(original_test + b'\n# harmless semantic edit still violates byte immutability\n')
                record['mutations'].append({'name': f'stage{stage}_protected_test_edit',
                                             'detected': not originals_ok(workspace, case)})
                test_path.write_bytes(original_test)
                adapter = workspace / case['immutable_files'][1]
                adapter_original = adapter.read_bytes()
                adapter.write_bytes(adapter_original + b'\n# still protected\n')
                record['mutations'].append({'name': f'stage{stage}_protected_adapter_edit',
                                             'detected': not originals_ok(workspace, case)})
                adapter.write_bytes(adapter_original)
                if not gate(workspace, case, checks)['passed']:
                    raise AssertionError('negative validation did not restore its reference workspace')
            result['cases'].append(record)
    result['all_passed'] = all(stage['passed'] for case in result['cases'] for stage in case['stages']) and all(
        mutation['detected'] for case in result['cases'] for mutation in case['mutations'])
    return result

if __name__ == '__main__':
    result = validate()
    if not (ROOT / 'FREEZE.json').exists():
        (ROOT / 'validation.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'all_passed': result['all_passed'], 'cases': len(result['cases']),
                      'stages': sum(len(case['stages']) for case in result['cases']),
                      'mutations': sum(len(case['mutations']) for case in result['cases'])}))
    raise SystemExit(0 if result['all_passed'] else 1)
