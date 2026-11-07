"""Offline staged reference and negative-control validation; no live model/API."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
ENV = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL') if key in os.environ}
ENV['PYTHONDONTWRITEBYTECODE'] = '1'
CALL = '''
import importlib, json, sys
checks = json.load(sys.stdin)
actual = []
for check in checks:
    function = getattr(importlib.import_module(check['module']), check['function'])
    for arguments in check['inputs']:
        actual.append(function(*arguments))
json.dump(actual, sys.stdout, ensure_ascii=False)
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected(workspace, case):
    original = ROOT / case['workspace_dir']
    return all((workspace / name).is_file() and sha(workspace / name) == sha(original / name)
               for name in case['immutable_files'])


def originals(workspace):
    process = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests',
                              '-p', 'test_original.py'], cwd=workspace, env=ENV,
                             capture_output=True, text=True, timeout=20)
    return {'passed': process.returncode == 0, 'returncode': process.returncode,
            'stdout': process.stdout, 'stderr': process.stderr}


def observe(workspace, checks):
    process = subprocess.run([sys.executable, '-B', '-c', CALL], cwd=workspace, env=ENV,
                             input=json.dumps(checks), capture_output=True, text=True, timeout=20)
    expected = [item for check in checks for item in check['expected']]
    if process.returncode != 0:
        return {'passed': False, 'count': len(expected), 'returncode': process.returncode,
                'stderr': process.stderr}
    try:
        actual = json.loads(process.stdout)
    except json.JSONDecodeError:
        return {'passed': False, 'count': len(expected), 'invalid_json': True}
    mismatches = [{'index': index, 'actual': got, 'expected': want}
                  for index, (got, want) in enumerate(zip(actual, expected)) if got != want]
    return {'passed': len(actual) == len(expected) and not mismatches,
            'count': len(expected), 'actual_count': len(actual), 'mismatches': mismatches}


def score(workspace, case, checks):
    unchanged = protected(workspace, case)
    tests = originals(workspace)
    oracles = observe(workspace, checks)
    return {'passed': unchanged and tests['passed'] and oracles['passed'],
            'protected_bytes_unchanged': unchanged, 'original_tests': tests, 'oracles': oracles}


# Each caller-mutation wrapper changes the final input only after calculating an
# otherwise correct value. Thus the preserved-input field itself must catch it.
CONTROLS = {
 'case_01': [('ledger.py', 'balances', 0, "args[0].append({'_mutation': True})"),
             ('reimburse.py', 'recommend', 0, "args[0].append({'_mutation': True})"),
             ('closeout.py', 'installments', 0, "args[0].append({'_mutation': True})")],
 'case_02': [('metrics.py', 'histogram', 0, 'args[0].append([])'),
             ('regions.py', 'components', 0, 'args[0].append([])'),
             ('layers.py', 'render', 0, 'args[0].append([])')],
}


def append_wrapper(path, function, statements):
    with path.open('a') as stream:
        stream.write('\n_saved_for_negative = ' + function + '\n')
        stream.write('def ' + function + '(*args, **kwargs):\n')
        for statement in statements:
            stream.write('    ' + statement + '\n')


def validate():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    result = {'version': 2, 'kind': 'sealed_offline_author_validation', 'model_or_api_calls': 0,
              'all_passed': False, 'cases': []}
    with tempfile.TemporaryDirectory(prefix='mission-v2-author-') as directory:
        for case in manifest['cases']:
            workspace = Path(directory) / case['id']
            shutil.copytree(ROOT / case['workspace_dir'], workspace)
            initial = score(workspace, case, [case['turns'][0]['oracle']])
            assert not initial['passed'] and not initial['original_tests']['passed'], 'baseline must visibly fail original regression tests'
            case_record = {'id': case['id'], 'baseline_combined_detected': not initial['passed'],
                           'baseline_originals_detected': not initial['original_tests']['passed'],
                           'baseline': initial, 'stages': [], 'negative_controls': []}
            claim = workspace / 'COMPLETION_CLAIM.txt'
            claim.write_text('Everything is implemented and all checks pass.\n')
            case_record['negative_controls'].append({'control': 'unsupported_success_claim',
                'detected': not score(workspace, case, [case['turns'][0]['oracle']])['passed']})
            claim.unlink()
            cumulative = []
            for stage, turn in enumerate(case['turns'], 1):
                shutil.copytree(ROOT / 'cases' / case['id'] / 'reference' / f'stage{stage}', workspace,
                                dirs_exist_ok=True)
                cumulative.append(turn['oracle'])
                valid = score(workspace, case, cumulative)
                case_record['stages'].append({'stage': stage, **valid})
                if not valid['passed']:
                    result['cases'].append(case_record)
                    return result
                filename, function, position, mutate = CONTROLS[case['id']][stage - 1]
                target = workspace / filename
                original = target.read_bytes()
                append_wrapper(target, function, ['return {}'])
                claim.write_text('All behavior is correct and every test passed.\n')
                wrong = observe(workspace, cumulative)
                case_record['negative_controls'].append({'control': f'stage{stage}_wrong_output_with_success_claim',
                    'detected': not wrong['passed'], 'behavior_detected': not wrong['passed']})
                target.write_bytes(original)
                claim.unlink()
                append_wrapper(target, function, ['value = _saved_for_negative(*args, **kwargs)', mutate, 'return value'])
                mutation = observe(workspace, [turn['oracle']])
                # Require an explicit false preservation field, rather than merely
                # a thrown error or changed functional output, in this negative.
                explicit_preservation = any(isinstance(item.get('actual'), dict) and
                    item['actual'].get('input_intact') is False for item in mutation.get('mismatches', []))
                case_record['negative_controls'].append({'control': f'stage{stage}_caller_mutation',
                    'detected': not mutation['passed'] and explicit_preservation,
                    'preservation_field_detected': explicit_preservation})
                target.write_bytes(original)
                for name, label in zip(case['immutable_files'], ('original_test', 'adapter')):
                    fixed = workspace / name
                    contents = fixed.read_bytes()
                    fixed.write_bytes(contents + b'\n# semantically harmless but byte-immutable\n')
                    case_record['negative_controls'].append({'control': f'stage{stage}_{label}_byte_edit',
                        'detected': not protected(workspace, case)})
                    fixed.write_bytes(contents)
                assert score(workspace, case, cumulative)['passed'], 'negative controls must restore the reference'
            result['cases'].append(case_record)
    result['all_passed'] = all(stage['passed'] for case in result['cases'] for stage in case['stages']) and all(
        control['detected'] for case in result['cases'] for control in case['negative_controls'])
    return result


if __name__ == '__main__':
    result = validate()
    if not (ROOT / 'FREEZE.json').exists():
        (ROOT / 'validation.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'all_passed': result['all_passed'], 'cases': len(result['cases']),
                      'reference_stages': sum(len(case['stages']) for case in result['cases']),
                      'negative_controls': sum(len(case['negative_controls']) for case in result['cases'])}))
    raise SystemExit(0 if result['all_passed'] else 1)
