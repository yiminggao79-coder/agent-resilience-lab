#!/usr/bin/env python3
"""Real follow-up turn with optional SIGKILL between file effect and checkpoint.

The parent owns both runner subprocesses. It kills only its own process group,
then starts a fresh runner reading the same durable state. No key is passed in
argv/config; each API worker loads only the authorized credential's first line.
This is a public development scenario, not new independent acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from agent_lab.budget import BudgetLedger,BudgetedModel,load_first_line_key
from agent_lab.model import OpenAIModel
from agent_lab.engine import Engine,public_status
from agent_lab.coding_tools import CodingTools
from agent_lab.store import Store
from agent_lab.sandbox import run_tests,observe_function
from scripts.deepseek_live import RecordingModel
from scripts.evaluate_acceptance import copy_project,digest_file,json_equal,implementation_snapshot


def run_phase(command,marker=None,timeout=900):
    if marker is not None and Path(marker).exists():raise ValueError('fault_marker_already_exists')
    env={'PATH':os.defpath,'LANG':'C.UTF-8'}
    proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,start_new_session=True)
    injected=False;deadline=time.monotonic()+timeout
    try:
        while proc.poll() is None:
            if marker is not None and Path(marker).is_file():
                try:body=json.loads(Path(marker).read_text())
                except json.JSONDecodeError:
                    time.sleep(.02);continue
                if body['pid']!=proc.pid:raise ValueError('fault_owner_mismatch')
                os.killpg(proc.pid,signal.SIGKILL);injected=True;break
            if time.monotonic()>=deadline:
                os.killpg(proc.pid,signal.SIGKILL);break
            time.sleep(.02)
        stdout,stderr=proc.communicate(timeout=10)
        # Child stdout contains bounded lifecycle metadata only; stderr is never
        # echoed, since arbitrary exception text is not an evidence channel.
        return {'pid':proc.pid,'returncode':proc.returncode,'injected':injected,
                'stdout':stdout.decode('utf-8',errors='replace')[:65536], 'stderr_bytes':len(stderr)}
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid,signal.SIGKILL)
        proc.wait()
        proc.stdout.close();proc.stderr.close()


def recovery_unchanged(before,after):
    fields=('operation_id','turn_id','sha256','inode','mtime_ns')
    return isinstance(before,dict) and isinstance(after,dict) and all(key in before and before[key]==after.get(key) for key in fields)


def effect_receipt(state):
    intent=state['pending']['intent'];path=Path(state['workspace'])/intent['args']['path']
    info=path.stat()
    return {'pid':os.getpid(),'operation_id':state['pending']['operation_id'],'turn_id':state['turn_id'],
            'call_id':state['pending']['call_id'],'path':intent['args']['path'],
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'inode':info.st_ino,'mtime_ns':info.st_mtime_ns}


def child(args,experiment):
    receipts=Path(args.experiment)/('start-receipts' if args.phase=='start' else 'resume-receipts')
    receipts.mkdir(mode=0o700)
    delegate=OpenAIModel('deepseek-flash','https://api.deepseek.com','DEEPSEEK_API_KEY',True,protocol='chat',
               stream=False,token_parameter='max_tokens',parallel_parameter=False,thinking='disabled',
               ca_bundle='/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').is_file() else None)
    model=RecordingModel(BudgetedModel(delegate,BudgetLedger(REPO/'.lab'/'paid-budget.sqlite')),receipts)
    marker=Path(args.experiment)/'effect-before-checkpoint.json'
    recovery=Path(args.experiment)/'effect-after-recovery.json'
    def hook(event,state):
        if event!='after_tool_effect' or state['pending']['intent']['policy']!='verify':return
        if args.phase=='start' and args.inject_kill:
            body=effect_receipt(state)
            with marker.with_suffix('.tmp').open('x') as stream:
                json.dump(body,stream);stream.flush();os.fsync(stream.fileno())
            os.replace(marker.with_suffix('.tmp'),marker)
            while True:signal.pause()
        elif args.phase=='resume' and marker.exists():
            before=json.loads(marker.read_text())
            if state['pending']['operation_id']==before['operation_id']:
                recovery.write_text(json.dumps(effect_receipt(state)))
    try:
        with Store(args.task_db) as store:
            original=store.load(args.task_id)
            root=Path(original['workspace'])
            if root.is_symlink() or not root.resolve().is_relative_to(REPO/'.lab'):
                raise ValueError('experiment_workspace_required')
            engine=Engine(store,CodingTools(root),model,hook=hook)
            if args.phase=='start':engine.continue_turn(args.task_id,experiment['scenario']['task'])
            load_first_line_key()
            state=engine.run(args.task_id)
            approvals=0
            while state['phase']=='awaiting_approval':
                intent=state['pending']['intent']
                if intent.get('name')!='run_command':break
                from agent_lab.commands import validate_command
                validate_command(root,intent)
                action=engine.approval_request(args.task_id)
                engine.decide_approval(args.task_id,action['request_id'],action['digest'],True)
                approvals+=1;state=engine.run(args.task_id)
            before=experiment['immutable_sha256']
            unchanged=all(digest_file(root/name)==want for name,want in before.items())
            result={'pid':os.getpid(),**public_status(state),'turn_records':store.turns(args.task_id),
                    'sandbox_commands_preapproved':approvals,'immutable_files_unchanged':unchanged,'new_oracle_matches':None,'old_oracle_matches':None,
                    'final_public_tests_exit_code':None,'model_requests':model.receipts,'trace':store.events(args.task_id)}
            if state['phase']=='completed' and unchanged:
                final=run_tests(root,12);result['final_public_tests_exit_code']=final['exit_code']
                oracle_root=Path(args.experiment)/'final-oracle';copy_project(root,oracle_root)
                for key in ('old','new'):
                    oracle=experiment[key+'_oracle']
                    observed=observe_function(oracle_root,oracle['module'],oracle['function'],oracle['inputs'],12)
                    result[key+'_oracle_matches']=bool(observed['complete'] and json_equal(observed['observations'],oracle['expected']))
            result['task_success']=bool(state['phase']=='completed' and unchanged and result['final_public_tests_exit_code']==0
                                        and result['new_oracle_matches'] and result['old_oracle_matches'])
            if marker.exists():
                call_id=json.loads(marker.read_text())['call_id']
                feedback=next((m for m in state['messages'] if m.get('role')=='tool' and m.get('tool_call_id')==call_id),None)
                payload=json.loads(feedback['content']) if feedback else {}
                result['recovered_effect_reconciled']=payload.get('reconciled') is True
            target=Path(args.experiment)/'result.json';target.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
            print(json.dumps({'phase':state['phase'],'task_success':result['task_success']}),flush=True)
            return 0 if result['task_success'] else 1
    finally:
        os.environ.pop('DEEPSEEK_API_KEY',None)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task-db',type=Path,required=True);p.add_argument('--task-id',required=True)
    p.add_argument('--scenario',type=Path,default=REPO/'examples/scenarios/idle-followup.json')
    p.add_argument('--inject-kill',action='store_true')
    p.add_argument('--phase',choices=('start','resume'));p.add_argument('--experiment',type=Path)
    args=p.parse_args(argv)
    if not args.task_db.resolve().is_relative_to(REPO/'.lab'):raise ValueError('experiment_database_required')
    if args.phase:
        experiment=json.loads((args.experiment/'experiment.json').read_text())
        return child(args,experiment)
    snapshot=implementation_snapshot()
    if snapshot['runtime_dirty']:
        print(json.dumps({'blocked':'commit_runtime_before_experiment','api_requests_sent':0}));return 2
    scenario=json.loads(args.scenario.read_text())
    manifest=json.loads((REPO/'acceptance_suite/v1/manifest.json').read_text())
    original=next(case for case in manifest['cases'] if case['id']==scenario['preserve_case'])
    with Store(args.task_db) as store:
        before=store.load(args.task_id);root=Path(before['workspace'])
        if before['phase']!='completed':raise ValueError('completed_previous_turn_required')
        immutable={str(path.relative_to(root)):digest_file(path) for path in (root/'tests').rglob('*.py')}
    run_id=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    private=REPO/'.lab'/'followup'/run_id;private.mkdir(parents=True,mode=0o700)
    experiment={'scenario':scenario,'old_oracle':original['oracle'],'new_oracle':scenario['oracle'],'immutable_sha256':immutable,
                'session_id':before['session_id'],'previous_turn_id':before['turn_id'],'previous_session_metrics':before.get('session_metrics')}
    (private/'experiment.json').write_text(json.dumps(experiment))
    # Archive the real completed first-turn checkpoint before adding feedback.
    with Store(args.task_db) as original_store, sqlite3.connect(private/'starting-checkpoint.sqlite') as backup:
        original_store.db.backup(backup)
    copy_project(root,private/'starting-project')
    base=[sys.executable,str(Path(__file__).resolve()),'--task-db',str(args.task_db.resolve()),'--task-id',args.task_id,'--experiment',str(private)]
    initial=run_phase(base+['--phase','start']+(['--inject-kill'] if args.inject_kill else []),private/'effect-before-checkpoint.json' if args.inject_kill else None)
    restarted=run_phase(base+['--phase','resume']) if initial['injected'] else None
    result=json.loads((private/'result.json').read_text()) if (private/'result.json').exists() else None
    marker=private/'effect-before-checkpoint.json';receipt=private/'effect-after-recovery.json'
    proof=recovery_unchanged(json.loads(marker.read_text()),json.loads(receipt.read_text())) if marker.exists() and receipt.exists() else False
    report={'run_id':run_id,'kind':'real_multi_turn_sigkill_development_experiment','implementation':snapshot,
            'initial_process':initial,'restart_process':restarted,'fault_window_observed':initial['injected'],
            'file_identity_unchanged_at_recovery':proof,'result':result,'budget':BudgetLedger(REPO/'.lab'/'paid-budget.sqlite').report(),
            'implementation_unchanged':implementation_snapshot()['source_sha256']==snapshot['source_sha256'],
            'limitations':['Public follow-up scenario after consumed v1 acceptance; not a new heldout success rate.',
                           'Fault waits after a verified file effect; it does not model arbitrary lost remote effects.',
                           'Recovery uses actual SIGKILL and a new process, not exception handling.']}
    report['recovery_verified']=bool(initial['injected'] and initial['returncode']==-signal.SIGKILL and restarted
          and restarted['pid']!=initial['pid'] and proof and result and result.get('recovered_effect_reconciled')
          and result['metrics']['recoveries']>=1)
    report['session_continuity_verified']=bool(result and result['session_id']==experiment['session_id']
          and result['turn_id']!=experiment['previous_turn_id'] and len(result['turn_records'])>=2)
    output=REPO/'evidence'/'followup'/run_id/'report.json';output.parent.mkdir(parents=True)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    print(json.dumps({'report':str(output.relative_to(REPO)),'task_success':bool(result and result['task_success']),
          'recovery_verified':report['recovery_verified'],'session_continuity_verified':report['session_continuity_verified'],
          'committed_upper_cny':report['budget']['committed_upper_cny']}))
    return 0 if result and result['task_success'] and (not args.inject_kill or report['recovery_verified']) else 1


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:
        print(json.dumps({'error_code':getattr(error,'code','experiment_internal_error'),
                          'error_type':type(error).__name__}),flush=True)
        sys.exit(2)
