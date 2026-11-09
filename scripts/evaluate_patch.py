#!/usr/bin/env python3
"""One public real-model patch/move/delete/plan task; not independent acceptance."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from agent_lab.budget import BudgetLedger,BudgetedModel,load_first_line_key
from agent_lab.model import OpenAIModel,ModelError
from agent_lab.tools import ToolError
from scripts.deepseek_live import RecordingModel,StreamMetadata
from scripts.evaluate_acceptance import run_case,copy_project,digest_file,implementation_snapshot


def grade_patch_trial(result,root):
    root=Path(root);events=result.get('trace',[])
    layout=all((root/name).is_file() for name in ('money.py','parsing.py')) and all(
        not (root/name).exists() for name in ('rounding.py','legacy_round.py'))
    patch=any(e.get('kind')=='tool_finished' and e.get('tool')=='apply_patch_text' and e.get('ok') is True for e in events)
    plan=any(e.get('kind')=='plan_updated' and e.get('ok') is True for e in events)
    return {**result,'functional_gate_success':bool(result.get('task_success')),
            'required_layout_matches':layout,'patch_text_used_successfully':patch,'plan_tool_used':plan,
            'patch_steps_committed':sum(e.get('kind')=='patch_step_finished' for e in events),
            'task_success':bool(result.get('task_success') and layout and patch and plan)}


def main():
    snapshot=implementation_snapshot()
    if snapshot['runtime_dirty']:
        print(json.dumps({'blocked':'commit_runtime_before_experiment','api_requests_sent':0}));return 2
    suite=REPO/'examples/patch-task';case=json.loads((suite/'manifest.json').read_text())
    fixture_hashes={str(path.relative_to(suite)):digest_file(path) for path in suite.rglob('*') if path.is_file()}
    run_id=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    private=REPO/'.lab/patch-live'/run_id;private.mkdir(parents=True,mode=0o700)
    ledger=BudgetLedger(REPO/'.lab/paid-budget.sqlite')
    report={'run_id':run_id,'kind':'public_real_llm_plan_patch_development','implementation':snapshot,
            'fixture_sha256':fixture_hashes,'model':'deepseek-flash','protocol':'chat','thinking':'disabled',
            'independent_heldout':False,'limitations':['Public mechanism exercise explicitly requests plan/patch interfaces.',
                'Layout and finite external values do not certify general refactoring ability.',
                'No fault injection in this paid run; process recovery has separate evidence.']}
    started=time.monotonic()
    try:
        observer=StreamMetadata()
        delegate=OpenAIModel('deepseek-flash','https://api.deepseek.com','DEEPSEEK_API_KEY',True,
                    stream=True,token_parameter='max_tokens',parallel_parameter=False,thinking='disabled',
                    ca_bundle='/etc/ssl/cert.pem',on_text=observer.on_text)
        model=RecordingModel(BudgetedModel(delegate,ledger),private,text_observer=observer)
        load_first_line_key()
        result=run_case(suite,case,private/'trial',model,read_only_paths=['tests/test_public.py'])
        report['result']=grade_patch_trial(result,private/'trial/project')
    except (ModelError,ToolError) as error:report['error_code']=error.code
    except Exception:report['error_code']='patch_experiment_internal_error'
    finally:
        os.environ.pop('DEEPSEEK_API_KEY',None)
        report['elapsed_seconds']=time.monotonic()-started
        report['budget']=ledger.report()
        report['model_requests']=model.receipts if 'model' in locals() else []
        report['implementation_unchanged']=implementation_snapshot()['source_sha256']==snapshot['source_sha256']
        report['fixture_unchanged']=all(digest_file(suite/name)==want for name,want in fixture_hashes.items())
        output=REPO/'evidence/patch-live'/run_id/'report.json';output.parent.mkdir(parents=True)
        try:
            copy_project(private/'trial/project',output.parent/'candidate')
            report['candidate_sha256']={str(p.relative_to(output.parent/'candidate')):digest_file(p)
                                       for p in (output.parent/'candidate').rglob('*') if p.is_file()}
        except (OSError,ValueError,ToolError):report['artifact_error']='candidate_export_failed'
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'report':str(output.relative_to(REPO)),'task_success':report.get('result',{}).get('task_success',False),
                     'error_code':report.get('error_code'),'committed_upper_cny':report['budget']['committed_upper_cny']}))
    return 0 if report.get('result',{}).get('task_success') and report['implementation_unchanged'] and report['fixture_unchanged'] else 1


if __name__=='__main__':sys.exit(main())
