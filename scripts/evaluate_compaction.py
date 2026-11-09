#!/usr/bin/env python3
"""Continue a real guidance session through paid semantic compaction and coding.

Public development experiment. Not a large-context benchmark or new holdout.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from agent_lab.budget import BudgetLedger,BudgetedModel,load_first_line_key
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits,public_status
from agent_lab.model import OpenAIModel,ModelError
from agent_lab.sandbox import run_tests,observe_function
from agent_lab.store import Store
from agent_lab.tools import ToolError
from scripts.deepseek_live import RecordingModel,StreamMetadata,coding_cycle
from scripts.evaluate_acceptance import copy_project,digest_file,json_equal,implementation_snapshot

TASK=('Continue the same normalization project. Preserve the previous contract, project rules and all existing tests. '
      'Extend public normalizer.normalize(values, exclude=None): when exclude is provided, normalize its strings using '
      'the same canonical rules and omit matching keys from the ordinary output. None preserves the old behavior. '
      'Do not mutate either input. Add regression tests, run tests after the last edit and verify the result.')
NEW_INPUTS=[[[" A "," B ","a"," Straße "],["b","STRASSE"]],[["Σ","ς","x"],[" σ "]],[[],["x"]],[[" A "," B "],None]]
NEW_EXPECTED=[["a"],["x"],[],["a","b"]]
ORACLE_WRAPPER='''import copy
import normalizer

def inspect(values, exclude=None):
    before_values=copy.deepcopy(values)
    before_exclude=copy.deepcopy(exclude)
    result=normalizer.normalize(values,exclude)
    return {'result':result,'values_unchanged':values==before_values,'exclude_unchanged':exclude==before_exclude}
'''
OLD_INPUTS=[[[" Straße ","STRASSE","ß"," SS ","","\t"]],[[" Σ ","ς","σ"," A ","a"," B "]],[[]]]
OLD_EXPECTED=[["strasse","ss"],["σ","a","b"],[]]


def summary_in_request(request,record):
    for message in request.get('messages',[]):
        if message.get('role')!='user' or not isinstance(message.get('content'),str):continue
        try:payload=json.loads(message['content'].split('\n',1)[-1])
        except (ValueError,TypeError):continue
        if isinstance(payload,dict) and payload.get('semantic_context') is True and all(
                payload.get(key)==record[key] for key in ('summary','source_sha256','covered_until')):
            return True
    return False


def installed_this_attempt(state,previous_generation):
    record=state.get('semantic_context') or {}
    attempt=state.get('last_compaction') or {}
    return bool(attempt.get('status')=='installed' and type(record.get('generation')) is int
                and record['generation']>previous_generation
                and all(record.get(key)==attempt.get(key) for key in ('source_sha256','covered_until')))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task-db',type=Path,required=True);p.add_argument('--task-id',required=True)
    args=p.parse_args(argv)
    if not args.task_db.resolve().is_relative_to(REPO/'.lab'):raise ValueError('experiment_database_required')
    snapshot=implementation_snapshot()
    if snapshot['runtime_dirty']:
        print(json.dumps({'blocked':'commit_runtime_before_experiment','api_requests_sent':0}));return 2
    run_id=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    private=REPO/'.lab/compaction'/run_id;private.mkdir(parents=True,mode=0o700)
    receipts=private/'receipts';receipts.mkdir(mode=0o700)
    ledger=BudgetLedger(REPO/'.lab/paid-budget.sqlite')
    report={'run_id':run_id,'kind':'real_session_semantic_compaction_and_coding_development',
            'implementation':snapshot,'model':'deepseek-flash','protocol':'chat','limitations':[
             'Continuation of consumed public guidance scenario; not new independent acceptance.',
             'A real short session compression/continuation experiment, not a large-context quality rate.',
             'Source hashes establish provenance, not summary faithfulness.']}
    started=time.monotonic()
    try:
        with Store(args.task_db) as store:
            original=store.load(args.task_id);root=Path(original['workspace'])
            if original['phase']!='completed' or not root.is_relative_to(REPO/'.lab'):raise ValueError('completed_experiment_session_required')
            with sqlite3.connect(private/'starting-checkpoint.sqlite') as backup:store.db.backup(backup)
            copy_project(root,private/'starting-project')
            immutable={str(path.relative_to(root)):digest_file(path) for path in (root/'tests').rglob('*.py')}
            observer=StreamMetadata()
            delegate=OpenAIModel('deepseek-flash','https://api.deepseek.com','DEEPSEEK_API_KEY',True,
                     stream=True,token_parameter='max_tokens',parallel_parameter=False,thinking='disabled',
                     ca_bundle='/etc/ssl/cert.pem',on_text=observer.on_text)
            model=RecordingModel(BudgetedModel(delegate,ledger),receipts,text_observer=observer)
            load_first_line_key()
            engine=Engine(store,CodingTools(root),model,observer=lambda e:print(json.dumps({k:e[k] for k in ('kind','phase','tool','code') if k in e}),flush=True))
            engine.continue_turn(args.task_id,TASK,Limits(max_steps=40,max_seconds=900,model_timeout=90,tool_timeout=12,max_output_tokens=4096,max_context_bytes=98304))
            archive=copy.deepcopy(store.load(args.task_id)['messages'])
            compacted=engine.compact(args.task_id)
            semantic=compacted.get('semantic_context')
            report['archive_unchanged_by_compaction']=compacted['messages']==archive
            report['semantic_record_present']=bool(semantic)
            report['semantic_record_sha256']=hashlib.sha256(json.dumps(semantic,sort_keys=True).encode()).hexdigest() if semantic else None
            report['last_compaction']=compacted.get('last_compaction')
            report['semantic_installed_this_attempt']=installed_this_attempt(compacted,(original.get('semantic_context') or {}).get('generation',0))
            if report['semantic_installed_this_attempt'] and compacted['phase']=='ready':
                state=engine.run(args.task_id);approvals=0
                while state['phase']=='awaiting_approval':
                    if state['pending']['intent'].get('name')!='run_command':break
                    from agent_lab.commands import validate_command
                    validate_command(root,state['pending']['intent'])
                    a=engine.approval_request(args.task_id)
                    engine.decide_approval(args.task_id,a['request_id'],a['digest'],True)
                    approvals+=1;state=engine.run(args.task_id)
                first_request=json.loads((receipts/'model-001-request.json').read_text())
                continuation=receipts/'model-002-request.json'
                report['summary_request_tools_disabled']=first_request.get('tools')==[]
                report['semantic_summary_in_continuation_request']=continuation.exists() and summary_in_request(json.loads(continuation.read_text()),semantic)
                unchanged=old_ok=new_ok=exit_code=None
                if state['phase']=='completed':
                    unchanged=all(digest_file(root/name)==want for name,want in immutable.items())
                    if unchanged:
                        exit_code=run_tests(root,12)['exit_code']
                        unchanged=all(digest_file(root/name)==want for name,want in immutable.items())
                    oracle_root=private/'oracle';copy_project(root,oracle_root)
                    old=observe_function(oracle_root,'normalizer','normalize',OLD_INPUTS,12)
                    (oracle_root/'external_oracle_probe.py').write_text(ORACLE_WRAPPER)
                    new=observe_function(oracle_root,'external_oracle_probe','inspect',NEW_INPUTS,12)
                    old_ok=old['complete'] and json_equal(old['observations'],OLD_EXPECTED)
                    new_ok=new['complete'] and json_equal(new['observations'],[
                        {'result':value,'values_unchanged':True,'exclude_unchanged':True} for value in NEW_EXPECTED])
                report['result']={**public_status(state),'original_tests_unchanged':unchanged,'old_oracle_matches':old_ok,
                      'new_oracle_matches':new_ok,'final_public_tests_exit_code':exit_code,
                      'confirmed_coding_cycle':coding_cycle(state['messages'][len(archive):]),'sandbox_commands_preapproved':approvals,
                      'same_session_new_turn':state['session_id']==original['session_id'] and state['turn_id']!=original['turn_id']}
                report['result']['task_success']=bool(state['phase']=='completed' and unchanged and old_ok and new_ok and exit_code==0 and
                        report['archive_unchanged_by_compaction'] and report['result']['same_session_new_turn'] and report['result']['confirmed_coding_cycle'] and
                        report['summary_request_tools_disabled'] and report['semantic_summary_in_continuation_request'] and report['semantic_installed_this_attempt'])
            report['trace']=store.events(args.task_id)
    except (ModelError,ToolError) as error:report['error_code']=error.code
    except Exception:report['error_code']='experiment_internal_error'
    finally:
        os.environ.pop('DEEPSEEK_API_KEY',None)
        report['elapsed_seconds']=time.monotonic()-started
        report['budget']=ledger.report()
        report['model_requests']=model.receipts if 'model' in locals() else []
        report['implementation_unchanged']=implementation_snapshot()['source_sha256']==snapshot['source_sha256']
        output=REPO/'evidence/compaction'/run_id/'report.json';output.parent.mkdir(parents=True)
        if 'root' in locals():
            try:
                copy_project(root,output.parent/'candidate')
                report['candidate_sha256']={str(path.relative_to(output.parent/'candidate')):digest_file(path) for path in (output.parent/'candidate').rglob('*') if path.is_file()}
            except (OSError,ValueError,ToolError):
                report['artifact_export_error']='candidate_export_failed'
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'report':str(output.relative_to(REPO)),'result':report.get('result'),'semantic_record_present':report.get('semantic_record_present'),
                     'error_code':report.get('error_code'),'committed_upper_cny':report['budget']['committed_upper_cny']}))
    return 0 if report.get('result',{}).get('task_success') and report['implementation_unchanged'] else 1


if __name__=='__main__':sys.exit(main())
