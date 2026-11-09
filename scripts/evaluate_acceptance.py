#!/usr/bin/env python3
"""Frozen independent coding tasks; expected values never enter candidate workspaces.

Real calls use only the user-authorized DeepSeek endpoint and shared CNY ledger.
Failure-driven implementation changes invalidate this suite as a new holdout.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import uuid

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from agent_lab.budget import BudgetLedger,BudgetedModel,load_first_line_key
from agent_lab.model import OpenAIModel,ModelError
from agent_lab.engine import Engine,Limits,public_status
from agent_lab.coding_tools import CodingTools
from agent_lab.store import Store
from agent_lab.sandbox import run_tests,observe_function,test_start_dir
from agent_lab.tools import ToolError
from scripts.deepseek_live import RecordingModel,coding_cycle


def safe_path(root,relative):
    if (not isinstance(relative,str) or not relative or Path(relative).is_absolute()
            or any(not p or p in ('.','..') or p.startswith('.') for p in relative.split('/'))):
        raise ValueError('invalid_fixture_path')
    current=Path(root)
    for part in relative.split('/'):
        current=current/part
        if current.is_symlink():raise ValueError('fixture_symlink')
    return current


def digest_file(path):
    fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>2*1024*1024:
            raise ValueError('invalid_fixture_file')
        return hashlib.sha256(stream.read(2*1024*1024+1)).hexdigest()


def verify_freeze(suite):
    suite=Path(suite).resolve()
    freeze=json.loads((suite/'FREEZE.json').read_text())
    files=freeze.get('files')
    if not isinstance(files,dict) or not files:raise ValueError('invalid_freeze')
    actual=set()
    for directory,dirs,names in os.walk(suite,followlinks=False):
        for name in dirs:
            if (Path(directory)/name).is_symlink():raise ValueError('fixture_symlink')
        for name in names:
            relative=str((Path(directory)/name).relative_to(suite))
            if relative!='FREEZE.json':actual.add(relative)
    if actual!=set(files):raise ValueError('freeze_inventory_mismatch')
    for relative,want in files.items():
        if not isinstance(want,str) or digest_file(safe_path(suite,relative))!=want:
            raise ValueError('freeze_mismatch')
    return hashlib.sha256((suite/'FREEZE.json').read_bytes()).hexdigest()


def json_equal(actual,expected):
    if type(actual) in (int,float) and type(expected) in (int,float):
        return math.isfinite(actual) and math.isfinite(expected) and actual==expected
    if type(actual)!=type(expected):return False
    if isinstance(actual,list):
        return len(actual)==len(expected) and all(json_equal(a,b) for a,b in zip(actual,expected))
    if isinstance(actual,dict):
        return actual.keys()==expected.keys() and all(json_equal(actual[k],expected[k]) for k in expected)
    return actual==expected


def copy_project(source,target):
    source,target=Path(source),Path(target)
    target.mkdir(parents=True)
    total,count=0,0
    for directory,dirs,files in os.walk(source,followlinks=False):
        dirs[:]=[name for name in dirs if not name.startswith('.') and name!='__pycache__']
        for name in dirs:
            if (Path(directory)/name).is_symlink():raise ValueError('candidate_symlink')
        relative=Path(directory).relative_to(source)
        (target/relative).mkdir(exist_ok=True)
        for name in files:
            if name.startswith('.') or name.endswith(('.pyc','.pyo')):continue
            path=Path(directory)/name
            digest_file(path)  # bounded, regular single-link, no-follow admission
            total+=path.stat().st_size;count+=1
            if count>200 or total>8*1024*1024:raise ValueError('candidate_limit')
            shutil.copyfile(path,target/relative/name,follow_symlinks=False)


def run_case(suite,case,trial,model,preapprove=False,*,read_only_paths=()):
    trial=Path(trial);trial.mkdir(parents=True)
    root=trial/'project'
    copy_project(safe_path(suite,case['workspace_dir']),root)
    before={name:digest_file(safe_path(root,name)) for name in case['immutable_files']}
    tools=CodingTools(root,read_only_paths=read_only_paths) if read_only_paths else CodingTools(root)
    test_options={'read_only_paths':tools.read_only_policy.paths} if read_only_paths else {}
    test_options['start_dir']=test_start_dir(root)
    baseline=run_tests(root,10,**test_options)
    if baseline['exit_code']==0:raise ValueError('acceptance_baseline_must_fail')
    limits=Limits(max_steps=40,max_seconds=900,model_timeout=90,tool_timeout=12,max_output_tokens=4096)
    def progress(event):
        print(json.dumps({k:event[k] for k in ('kind','phase','tool','code') if k in event}),flush=True)
    started=time.monotonic();approvals=0
    with Store(trial/'tasks.sqlite') as store:
        engine=Engine(store,tools,model,observer=progress)
        tid=engine.create(case['task'],limits);state=engine.run(tid)
        while state['phase']=='awaiting_approval' and preapprove:
            intent=state['pending']['intent']
            if intent.get('name')!='run_command':break
            from agent_lab.commands import validate_command
            validate_command(root,intent)
            approval=engine.approval_request(tid)
            engine.decide_approval(tid,approval['request_id'],approval['digest'],True)
            approvals+=1;state=engine.run(tid)
        final_public=None
        unchanged=matches=None
        observed={'complete':False,'exit_code':None}
        # Unknown/running effects are not a safe time to launch more candidate
        # code, and absence of observations is not a measured wrong answer.
        if state['phase']=='completed':
            try:
                unchanged=all(digest_file(safe_path(root,name))==want for name,want in before.items())
                if unchanged:
                    final_public=run_tests(root,12,**test_options)
                    unchanged=all(digest_file(safe_path(root,name))==want for name,want in before.items())
                oracle_root=trial/'oracle';copy_project(root,oracle_root)
                oracle=case['oracle']
                observed=observe_function(oracle_root,oracle['module'],oracle['function'],oracle['inputs'],12)
                matches=observed['complete'] and json_equal(observed['observations'],oracle['expected'])
            except (OSError,ValueError,ToolError):
                observed={'complete':False,'exit_code':None};matches=False
        cycle=coding_cycle(state['messages'])
        success=state['phase']=='completed' and unchanged and matches and cycle and final_public is not None and final_public['exit_code']==0
        return {'case':case['id'],**public_status(state),'task_success':bool(success),
                'immutable_files_unchanged':unchanged,'oracle_matches':matches,
                'final_public_tests_exit_code':final_public['exit_code'] if final_public else None,
                'observations_complete':observed['complete'],'oracle_exit_code':observed['exit_code'],
                'confirmed_coding_cycle':cycle,'sandbox_commands_preapproved':approvals,
                'elapsed_seconds':time.monotonic()-started,'trace':store.events(tid)}


def implementation_snapshot():
    sources={str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest()
             for parent in (REPO/'agent_lab',REPO/'scripts') for p in sorted(parent.rglob('*.py'))}
    sources[str(Path(__file__).relative_to(REPO))]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    env={'PATH':os.defpath,'LANG':'C'}
    commit=subprocess.run(['git','rev-parse','HEAD'],cwd=REPO,env=env,capture_output=True,text=True,check=True).stdout.strip()
    dirty=subprocess.run(['git','status','--porcelain','--untracked-files=all','--','agent_lab','scripts'],
                         cwd=REPO,env=env,capture_output=True,text=True,check=True).stdout
    return {'commit':commit,'source_sha256':sources,'runtime_dirty':bool(dirty)}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--suite',type=Path,default=REPO/'acceptance_suite'/'v1')
    p.add_argument('--case',default=None)
    p.add_argument('--preapprove-sandbox-commands',action='store_true')
    p.add_argument('--protect-existing-files',action='store_true',help='enforce each case immutable_files as host read-only scopes, including test execution')
    p.add_argument('--model',choices=('deepseek-flash','deepseek-v4-pro'),default='deepseek-flash')
    p.add_argument('--stream',action='store_true')
    args=p.parse_args(argv)
    snapshot=implementation_snapshot()
    if snapshot['runtime_dirty']:
        print(json.dumps({'blocked':'commit_runtime_before_independent_acceptance','api_requests_sent':0}));return 2
    freeze_sha=verify_freeze(args.suite)
    manifest=json.loads((args.suite/'manifest.json').read_text())
    cases=[case for case in manifest['cases'] if args.case is None or case['id']==args.case]
    if not cases:raise ValueError('unknown_case')
    ledger=BudgetLedger(REPO/'.lab'/'paid-budget.sqlite')
    run_id=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    private=REPO/'.lab'/'acceptance'/run_id;private.mkdir(parents=True,mode=0o700)
    report={'run_id':run_id,'kind':'real_llm_frozen_independent_acceptance','suite_freeze_sha256':freeze_sha,
            'implementation':snapshot,'model':args.model,'protocol':'chat','stream':args.stream,'thinking':'disabled',
            'approval_policy':'preauthorized_sandbox_fixture_commands' if args.preapprove_sandbox_commands else 'ask',
            'immutable_files_host_protected':args.protect_existing_files,
            'cases':[],'limitations':['Two finite Python tasks, not general coding success or tamperproof malicious-code judging.',
                'No reference Codex comparison, no live recovery injection in this suite.',
                'Any task-driven implementation tuning declassifies this snapshot as development data.']}
    try:
        load_first_line_key()
        delegate=OpenAIModel(args.model,'https://api.deepseek.com','DEEPSEEK_API_KEY',True,
                    protocol='chat',stream=args.stream,token_parameter='max_tokens',parallel_parameter=False,
                    thinking='disabled',ca_bundle='/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').is_file() else None)
        for case in cases:
            receipts=private/(case['id']+'-receipts');receipts.mkdir(mode=0o700)
            model=RecordingModel(BudgetedModel(delegate,ledger),receipts)
            result=run_case(args.suite,case,private/case['id'],model,args.preapprove_sandbox_commands,
                            read_only_paths=case['immutable_files'] if args.protect_existing_files else ())
            result['model_requests']=model.receipts;report['cases'].append(result)
            if ledger.report()['halted']:break
    except (ModelError,ToolError) as error:
        report['error_code']=error.code
    except Exception:
        report['error_code']='acceptance_internal_error'
    finally:
        os.environ.pop('DEEPSEEK_API_KEY',None)
        report['budget']=ledger.report()
        report['suite_still_frozen']=verify_freeze(args.suite)==freeze_sha
        final=implementation_snapshot()
        report['implementation_unchanged']=final['source_sha256']==snapshot['source_sha256']
        rows=report['cases']
        report['tasks_attempted']=len(rows)
        report['task_success_rate']=sum(row['task_success'] for row in rows)/len(rows) if rows else None
        output=REPO/'evidence'/'acceptance'/run_id/'report.json';output.parent.mkdir(parents=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'report':str(output.relative_to(REPO)),'tasks_attempted':report['tasks_attempted'],
                      'task_success_rate':report['task_success_rate'],'error_code':report.get('error_code'),
                      'committed_upper_cny':report['budget']['committed_upper_cny']}))
    return 0 if rows and len(rows)==len(cases) and all(row['task_success'] for row in rows) and report['implementation_unchanged'] else 1


if __name__=='__main__':sys.exit(main())
