"""Offline noneditable-install smoke; real tools, synthetic model, no API.

Build/install a wheel into a separate venv first; pass its Python explicitly.
Only fresh public fixtures are executed. No private project state is copied.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


CHILD = r'''
import contextlib, hashlib, importlib.metadata, io, itertools, json, os, sys
from pathlib import Path
from unittest.mock import patch
import agent_lab
from agent_lab import __main__ as cli, native_jobs
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store

base=Path(sys.argv[1]); root=base/'target'; runtime=Path(agent_lab.__file__).resolve().parent
assert Path(sys.prefix).resolve() in runtime.parents
assert root not in runtime.parents and runtime not in root.parents
distribution=importlib.metadata.distribution('agent-resilience-lab')
direct=json.loads(distribution.read_text('direct_url.json') or '{}')
assert not direct.get('dir_info',{}).get('editable',False)
assert distribution.version=='0.1.0'
package_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in runtime.glob('*.py')}

call_ids=itertools.count()
def response(name=None,args=None):
    message={'role':'assistant','content':'synthetic wiring only'}
    if name:
        message['tool_calls']=[{'id':'call-'+str(next(call_ids)),'type':'function',
            'function':{'name':name,'arguments':json.dumps(args or {})}}]
    return {'message':message,'usage':{'input_tokens':1,'output_tokens':1},'finish_reason':'stop'}

class SyntheticModel:
    identity='offline_installed_runtime_smoke'
    def __init__(self):
        self.replies=iter([response('read_file',{'path':'calc.py'}),response('run_tests'),
            response('apply_patch',{'path':'calc.py','old':'return a - b','new':'return a + b'}),
            response('run_tests'),response()])
    def complete(self, messages, tools, timeout, max_tokens): return next(self.replies)

store=Store(base/'tasks.sqlite')
try:
    tools=CodingTools(root)
    engine=Engine(store,tools,SyntheticModel())
    tid=engine.create('Public synthetic installed-runtime wiring check',
                      Limits(tool_timeout=10,max_seconds=60),track_file_context=True)
    state=engine.run(tid)
    assert state['phase']=='completed', state.get('reason')
    results=[json.loads(m['content']) for m in state['messages'] if m['role']=='tool']
    tests=[r for r in results if 'exit_code' in r]
    assert len(tests)==2 and tests[0]['exit_code']==1 and tests[1]['exit_code']==0, results
    assert not tests[0]['ok'] and tests[1]['ok']
    assert (root/'calc.py').read_text()=='def add(a, b): return a + b\n'
    job=native_jobs.inspect_workspace(root,base/'tasks.sqlite')
    assert job['state']=='quiescent' and job['proof_kind']=='all_native_joined'
    assert not (root/'import-hijacked').exists()
    overlap=[]
    for candidate in (runtime,runtime.parent,runtime/'__pycache__'):
        candidate.mkdir(exist_ok=True)
        try: CodingTools(candidate)
        except ValueError as error:
            assert str(error)=='workspace_overlaps_trusted_runtime';overlap.append(True)
        else: raise AssertionError('runtime_overlap_accepted')
    for selected in (None,base/'missing-ledger.sqlite'):
        arguments=['--db',str(base/'admission.sqlite'),'run','Synthetic admission only',
            '--workspace',str(root),'--model','deepseek-flash','--base-url','https://api.deepseek.com',
            '--api-key-env','DEEPSEEK_API_KEY','--token-parameter','max_tokens','--allow-api','--load-deepseek-key']
        if selected is not None:arguments+=['--budget-ledger',str(selected)]
        with patch('agent_lab.budget.load_first_line_key') as loader, \
             patch.object(cli.OpenAIModel,'complete',side_effect=AssertionError('provider_forbidden')) as provider, \
             contextlib.redirect_stdout(io.StringIO()) as output, \
             contextlib.redirect_stderr(io.StringIO()) as errors:
            code=cli.main(arguments)
        assert code==2 and not output.getvalue()
        assert json.loads(errors.getvalue())['error']=='budget_ledger_unavailable'
        assert loader.call_count==provider.call_count==0
    assert not (base/'missing-ledger.sqlite').exists()
    assert not (runtime.parent/'.lab'/'paid-budget.sqlite').exists()
    print(json.dumps({'installed_version':distribution.version,'editable':False,'package_hashes':package_hashes,
        'runtime_inside_selected_venv':True,'target_shadow_imported':False,
        'engine_phase':state['phase'],'native_test_exit_codes':[r['exit_code'] for r in tests],
        'patch_exact_bytes_verified':True,'native_job_quiescent':True,'overlap_rejections':len(overlap),
        'missing_ledger_admission_cases':2,'credential_loader_calls':0,'provider_requests':0}))
finally: store.close()
'''


def validate(python, wheel):
    python = Path(python).absolute()
    wheel = Path(wheel).resolve(strict=True)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        source_hashes = {}
        for name in names:
            parts = Path(name).parts
            if len(parts) != 2 or parts[0] not in ('agent_lab', 'agent_resilience_lab-0.1.0.dist-info'):
                raise ValueError('unexpected_wheel_inventory')
            if parts[0] == 'agent_lab':
                if not parts[1].endswith('.py'): raise ValueError('unexpected_runtime_payload')
                source_hashes[parts[1]] = hashlib.sha256(archive.read(name)).hexdigest()
        if not source_hashes: raise ValueError('missing_runtime_payload')
    env = {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}
    with tempfile.TemporaryDirectory(prefix='agentlab-installed-smoke-',dir='/private/tmp') as directory:
        base = Path(directory); root = base/'target'; root.mkdir()
        (root/'agent_lab').mkdir()
        (root/'agent_lab'/'__init__.py').write_text("from pathlib import Path\nPath('import-hijacked').write_text('bad')\nraise RuntimeError('counterfeit package')\n")
        (root/'agent_lab'/'__main__.py').write_text("raise RuntimeError('counterfeit CLI')\n")
        (root/'calc.py').write_text('def add(a, b): return a - b\n')
        (root/'tests').mkdir()
        (root/'tests'/'test_calc.py').write_text(
            'import unittest, calc, os, socket, subprocess, sys\n'
            'class T(unittest.TestCase):\n'
            ' def test_add(self): self.assertEqual(calc.add(2, 3), 5)\n'
            ' def test_boundaries(self):\n'
            '  self.assertNotIn("DEEPSEEK_API_KEY", os.environ)\n'
            '  with self.assertRaises(OSError): os.fork()\n'
            '  with self.assertRaises(OSError): subprocess.Popen([sys.executable,"-c","pass"])\n'
            '  with self.assertRaises(OSError): socket.socket().connect(("127.0.0.1",9))\n')
        invocations = ([str(python),'-I','-m','agent_lab','providers'],
                       [str(python.parent/'agent-lab'),'providers'])
        for argv in invocations:
            result = subprocess.run(argv,cwd=root,env=env,capture_output=True,text=True,timeout=15)
            if result.returncode or not isinstance(json.loads(result.stdout),list):
                raise RuntimeError('installed_cli_smoke_failed')
        result = subprocess.run([str(python),'-I','-c',CHILD,str(base)],cwd=root,env=env,
                                capture_output=True,text=True,timeout=80)
        if result.returncode:
            # This child uses only fresh public fixtures, but return a fixed
            # failure at the public boundary; inspect locally while developing.
            raise RuntimeError('installed_tool_smoke_failed')
        report = json.loads(result.stdout)
        if report.pop('package_hashes') != source_hashes:
            raise RuntimeError('installed_payload_differs_from_wheel')
        if (root/'import-hijacked').exists(): raise RuntimeError('target_shadow_imported')
    report.update({'kind':'offline_noneditable_install_and_native_tools',
        'wheel_sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),
        'wheel_entries':len(names),'runtime_source_files':len(source_hashes),
        'isolated_module_cli':True,'console_cli':True,'wheel_payload_matches_install':True,
        'real_llm_task_result':False,'original_budget_opened':False,'credential_file_read':False})
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python',required=True,help='trusted noneditable venv Python outside the target')
    parser.add_argument('--wheel',required=True,help='the exact local public-only wheel installed there')
    args=parser.parse_args()
    print(json.dumps(validate(args.python,args.wheel),indent=2))
