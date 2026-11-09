#!/usr/bin/env python3
"""V3 quality judging, separate from V1/V2 semantics and model self-claims.

Development tests use different synthetic suites. The frozen V3 package is
interpreted only by an authorized evaluation run, never for implementation tuning.
"""
from decimal import Decimal, InvalidOperation
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agent_lab.budget import INPUT_CEILING, RATES, BudgetLedger, BudgetedModel, load_first_line_key
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits, public_status
from agent_lab.native_jobs import JobBlocked, JobStateError
from agent_lab.sandbox import run_tests, observe_function, test_start_dir
from agent_lab.store import Store, Busy, StorageUnavailable
from agent_lab.tools import ToolError
from agent_lab.model import OpenAIModel,ModelError
from scripts.deepseek_live import coding_cycle,RecordingModel
from scripts.evaluate_acceptance import (safe_path, digest_file, copy_project, json_equal,
                                        verify_freeze, implementation_snapshot)
from scripts.holdout_gate import ExposureGate,ExposureModel,ExposureError,ExposureBlocked

SCORE_VERSION = 'v3_quality_1'


def budget_preflight(report, model, max_tokens=4096):
    """Permission upper bounds, not a supplier balance or an API reservation."""
    try:
        if model not in RATES or type(max_tokens) is not int or not 1 <= max_tokens <= 393216:
            raise ValueError()
        values = [Decimal(str(report[key])) * 1000000 for key in ('cap_cny','committed_upper_cny')]
        if any(not n.is_finite() or n < 0 or n != n.to_integral_value() for n in values):
            raise ValueError()
        cap, used = map(int, values)
        unresolved = report['unresolved_requests']
        if type(unresolved) is not int or unresolved < 0 or type(report['halted']) is not bool:
            raise ValueError()
        quote = INPUT_CEILING * RATES[model][0] + max_tokens * RATES[model][1]
        available = max(0, cap-used)
    except (ValueError, KeyError, TypeError, InvalidOperation, OverflowError):
        raise ValueError('invalid_budget_preflight') from None
    return {'allowed':not report['halted'] and available >= quote,
            'minimum_reservation_cny':quote/1000000, 'available_upper_cny':available/1000000,
            'unresolved_requests':unresolved}


def paid_preflight(report,model,max_tokens=4096,*,prepaid_account=False):
    if prepaid_account:
        return {'allowed':not report['halted'],'authorization':'prepaid_account','project_cap_enforced':False,
                'unresolved_requests':report['unresolved_requests']}
    return budget_preflight(report,model,max_tokens)


def grade_quality(row):
    """Known failed constraints suffice; absence is never a fabricated result."""
    immutable = row.get('immutable_files_unchanged')
    test_exit = row.get('final_public_tests_exit_code')
    valid = row.get('oracle_valid_result')
    matches = row.get('oracle_matches')
    if (immutable is False or (type(test_exit) is int and test_exit != 0)
            or valid is False or matches is False):
        return False
    if immutable is True and type(test_exit) is int and test_exit == 0 and valid is True and matches is True:
        return True
    return None


def summarize_quality(rows):
    measured = [row['quality_passed'] for row in rows if type(row.get('quality_passed')) is bool]
    passes = sum(value is True for value in measured)
    return {'quality_measured':len(measured),'quality_unmeasured':len(rows)-len(measured),
            'quality_passes':passes,'quality_pass_rate':passes/len(measured) if measured else None}


def _validate_case(suite, case):
    try:
        if (not isinstance(case, dict) or not isinstance(case['id'],str)
                or re.fullmatch('[a-z0-9][a-z0-9_-]{0,79}',case['id']) is None
                or not isinstance(case['task'],str) or not 1 <= len(case['task'].encode('utf-8')) <= 65536):
            raise ValueError()
        source = safe_path(suite,case['workspace_dir'])
        if not source.is_dir():raise ValueError()
        names=case['immutable_files'];oracle=case['oracle']
        if (not isinstance(names,list) or not 1 <= len(names) <= 64
                or any(not isinstance(name,str) for name in names) or len(set(names))!=len(names)):
            raise ValueError()
        for name in names:digest_file(safe_path(source,name))
        if (not isinstance(oracle,dict)
                or any(not isinstance(oracle[key],str) or re.fullmatch('[a-zA-Z][a-zA-Z0-9_]{0,79}',oracle[key]) is None
                       for key in ('module','function'))
                or not isinstance(oracle['inputs'],list) or not 1 <= len(oracle['inputs']) <= 512
                or any(not isinstance(args,list) for args in oracle['inputs'])
                or not isinstance(oracle['expected'],list) or len(oracle['expected']) != len(oracle['inputs'])):
            raise ValueError()
        if len(json.dumps(oracle,ensure_ascii=True,allow_nan=False).encode())>65536:
            raise ValueError()
        return source,names,oracle
    except (ValueError,TypeError,KeyError,OSError,UnicodeError,RecursionError):
        raise ValueError('invalid_v3_case') from None


def _safe_to_observe(state):
    return (state.get('phase') in ('completed','failed') and state.get('pending') is None
            and not state.get('tool_queue') and not state.get('outcome_unresolved'))


def run_case_v3(suite,case,trial,model,preapprove=False,*,limits=None,observer=None,observation_allowed=None):
    """One caller-supplied model attempt; real main wraps budget/exposure/receipts.

    Native judging uses fresh copies, never the unresolved original workspace.
    This function does not itself label a model as a real provider or a mock.
    """
    source,names,oracle=_validate_case(suite,case)
    if observation_allowed is not None and not callable(observation_allowed):
        raise ValueError('invalid_observation_gate')
    trial=Path(trial);trial.mkdir(parents=True,mode=0o700)
    root=trial/'project';copy_project(source,root)
    before={name:digest_file(safe_path(root,name)) for name in names}
    selected=test_start_dir(root)
    baseline_root=trial/'baseline';copy_project(root,baseline_root)
    baseline=run_tests(baseline_root,12,read_only_paths=tuple(names),start_dir=selected)
    if baseline['exit_code']==0:raise ValueError('acceptance_baseline_must_fail')
    tools=CodingTools(root,read_only_paths=tuple(names))
    limits=limits or Limits(max_steps=40,max_seconds=900,model_timeout=90,tool_timeout=12,max_output_tokens=4096)
    started=time.monotonic();approvals=0
    with Store(trial/'tasks.sqlite') as store:
        engine=Engine(store,tools,model,observer=observer)
        tid=engine.create(case['task'],limits);state=engine.run(tid)
        while state['phase']=='awaiting_approval' and preapprove:
            intent=state['pending']['intent']
            if intent.get('name')!='run_command':break
            from agent_lab.commands import validate_command
            validate_command(root,intent,read_only_paths=tuple(names))
            approval=engine.approval_request(tid)
            engine.decide_approval(tid,approval['request_id'],approval['digest'],True)
            approvals+=1;state=engine.run(tid)
        row={'case':case['id'],**public_status(state),'score_version':SCORE_VERSION,
             'immutable_files_unchanged':None,'final_public_tests_exit_code':None,
             'observations_complete':None,'oracle_exit_code':None,'oracle_valid_result':None,
             'oracle_matches':None,'observation_status':'blocked_unresolved_execution',
             'confirmed_coding_cycle':coding_cycle(state['messages']),
             'sandbox_commands_preapproved':approvals,'test_start_dir':selected}
        allowed=observation_allowed is None or observation_allowed() is True
        if not allowed:
            row['observation_status']='blocked_no_model_observation'
        if allowed and _safe_to_observe(state):
            try:
                with store.lock(root):
                    row['immutable_files_unchanged']=all(digest_file(safe_path(root,name))==want
                                                          for name,want in before.items())
                    if row['immutable_files_unchanged']:
                        test_root=trial/'final-tests';oracle_root=trial/'oracle'
                        copy_project(root,test_root);copy_project(root,oracle_root)
                if not row['immutable_files_unchanged']:
                    row['observation_status']='constraint_violation'
                else:
                    checked=run_tests(test_root,12,read_only_paths=tuple(names),start_dir=selected)
                    row['final_public_tests_exit_code']=checked['exit_code']
                    row['immutable_files_unchanged']=all(digest_file(safe_path(test_root,name))==want
                                                          for name,want in before.items())
                    observed=observe_function(oracle_root,oracle['module'],oracle['function'],oracle['inputs'],12,
                                              read_only_paths=tuple(names))
                    row['immutable_files_unchanged']=(row['immutable_files_unchanged'] is True and
                        all(digest_file(safe_path(oracle_root,name))==want for name,want in before.items()))
                    row.update(observations_complete=observed['complete'],oracle_exit_code=observed['exit_code'],
                               oracle_valid_result=observed['complete'],observation_status='observed')
                    if observed['complete']:
                        row['oracle_matches']=json_equal(observed['observations'],oracle['expected'])
            except (ToolError,StorageUnavailable,JobBlocked,JobStateError,Busy,OSError,ValueError):
                row['observation_status']='observation_unavailable'
        row['quality_passed']=grade_quality(row)
        row['elapsed_seconds']=time.monotonic()-started
        row['trace']=store.events(tid)
        return row


def _load_suite(suite):
    freeze=verify_freeze(suite)
    try:
        manifest_path=Path(suite)/'manifest.json'
        if manifest_path.stat().st_size>2*1024*1024:raise ValueError()
        manifest=json.loads(manifest_path.read_text())
        cases=manifest['cases']
        if not isinstance(cases,list) or not 1<=len(cases)<=10:raise ValueError()
        for case in cases:_validate_case(suite,case)
        if len({case['id'] for case in cases})!=len(cases):raise ValueError()
        return freeze,cases
    except (ValueError,KeyError,TypeError,OSError,UnicodeError,RecursionError):
        raise ValueError('invalid_v3_suite') from None


def _probe_backend(root):
    root=Path(root);root.mkdir(mode=0o700)
    (root/'test_backend.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_backend(self):\n  self.assertEqual(1,1)\n')
    result=run_tests(root,5)
    return result['exit_code']==0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',type=Path,default=REPO/'evaluation'/'holdout_v3')
    parser.add_argument('--allow-api',action='store_true',help='enable the already-authorized paid one-attempt experiment within shared budget')
    parser.add_argument('--model',choices=tuple(RATES),default='deepseek-flash')
    parser.add_argument('--prepaid-account',action='store_true',help='explicit prepaid-balance authorization; no project total cap, historical usage retained')
    parser.add_argument('--stream',action='store_true')
    parser.add_argument('--preapprove-sandbox-commands',action='store_true')
    args=parser.parse_args(argv)
    try:
        snapshot=implementation_snapshot()
        if snapshot['runtime_dirty']:
            print(json.dumps({'blocked':'commit_runtime_before_acceptance','api_requests_sent':0}))
            return 2
        ledger=BudgetLedger(REPO/'.lab'/'paid-budget.sqlite')
        permission=paid_preflight(ledger.report(),args.model,prepaid_account=args.prepaid_account)
        if not permission['allowed']:
            print(json.dumps({'blocked':'budget_unavailable','budget_preflight':permission,'api_requests_sent':0}))
            return 2
        if not args.allow_api:
            print(json.dumps({'mode':'preflight_only','budget_allows_request':True,'suite_checked':False,
                              'backend_checked':False,'api_requests_sent':0,'budget_preflight':permission}))
            return 0
        freeze,cases=_load_suite(args.suite)
        run_id=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        private=REPO/'.lab'/'acceptance-v3'/run_id;private.mkdir(parents=True,mode=0o700)
        if not _probe_backend(private/'backend-probe'):
            print(json.dumps({'blocked':'native_backend_unavailable','api_requests_sent':0}))
            return 2
    except (ValueError,OSError,StorageUnavailable,ToolError,KeyError,sqlite3.Error):
        print(json.dumps({'blocked':'preflight_unavailable','api_requests_sent':0}))
        return 2
    report={'kind':'real_provider_acceptance_v3_attempt','score_version':SCORE_VERSION,'run_id':run_id,
            'suite_freeze_sha256':freeze,'implementation':snapshot,'model':args.model,'protocol':'chat',
            'stream':args.stream,'thinking':'disabled','billing_authorization':'prepaid_account' if args.prepaid_account else 'legacy_project_cap_30','limits':asdict(Limits(max_steps=40,max_seconds=900,
                       model_timeout=90,tool_timeout=12,max_output_tokens=4096)),
            'approval_policy':'preauthorized_sandbox_commands' if args.preapprove_sandbox_commands else 'ask',
            'immutable_files_host_protected':True,'cases_planned':len(cases),'cases':[],
            'limitations':['Finite Python tasks; same AI environment, not external statistical independence.',
                'Oracle bootstrap is not a tamperproof malicious-code judge.',
                'Quality rate includes measured constraints only; missing observations remain null.',
                'Possible exposure precedes delegate access, not proof of provider delivery.',
                'Original and verification roots are distinct; uncertain artifacts are retained.']}
    previous_key=os.environ.get('DEEPSEEK_API_KEY');key_loaded=False;report_unavailable=False
    try:
        for case in cases:
            permission=paid_preflight(ledger.report(),args.model,prepaid_account=args.prepaid_account)
            if not permission['allowed']:
                report['cases'].append({'case':case['id'],'quality_passed':None,
                    'observation_status':'blocked_budget','exposure_possible':False})
                continue
            try:
                with ExposureGate(REPO/'.lab'/'acceptance-v3-exposures',freeze,case['id'],run_id) as exposure:
                    if not key_loaded:
                        load_first_line_key();key_loaded=True
                    delegate=OpenAIModel(args.model,'https://api.deepseek.com','DEEPSEEK_API_KEY',True,
                        protocol='chat',stream=args.stream,token_parameter='max_tokens',parallel_parameter=False,
                        thinking='disabled',ca_bundle='/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').is_file() else None)
                    receipts=private/(case['id']+'-receipts');receipts.mkdir(mode=0o700)
                    model=RecordingModel((BudgetedModel(ExposureModel(delegate,exposure),ledger,prepaid_account=True) if args.prepaid_account else BudgetedModel(ExposureModel(delegate,exposure),ledger)),receipts)
                    try:
                        row=run_case_v3(args.suite,case,private/case['id'],model,args.preapprove_sandbox_commands,
                            observation_allowed=lambda:exposure.exposure_possible and any(
                                item.get('outcome')=='complete' for item in model.receipts))
                    except (ModelError,ToolError,StorageUnavailable,OSError,ValueError,ExposureError):
                        row={'case':case['id'],'quality_passed':None,'observation_status':'attempt_unavailable'}
                    row['exposure_possible']=exposure.exposure_possible
                    row['model_receipts']=model.receipts
                    report['cases'].append(row)
            except ExposureBlocked:
                report['cases'].append({'case':case['id'],'quality_passed':None,
                    'observation_status':'blocked_prior_exposure','exposure_possible':False})
            except (ExposureError,OSError,ValueError,ModelError):
                report['cases'].append({'case':case['id'],'quality_passed':None,
                    'observation_status':'exposure_or_configuration_unavailable','exposure_possible':None})
    except KeyboardInterrupt:
        report['error_code']='interrupted'
    except (OSError,sqlite3.Error,StorageUnavailable,ValueError):
        report['error_code']='attempt_unavailable'
    finally:
        if key_loaded:
            if previous_key is None:os.environ.pop('DEEPSEEK_API_KEY',None)
            else:os.environ['DEEPSEEK_API_KEY']=previous_key
        try:
            report['budget']=ledger.report()
            if args.prepaid_account:
                report['budget']['legacy_configured_cap_cny']=report['budget'].pop('cap_cny')
                report['budget'].update(project_cap_enforced=False,authorization='prepaid_account')
            try:
                report['suite_still_frozen']=verify_freeze(args.suite)==freeze
                report['implementation_unchanged']=implementation_snapshot()['source_sha256']==snapshot['source_sha256']
            except (ValueError,OSError,KeyError):
                report['suite_still_frozen']=report['implementation_unchanged']=False
            observed_summary=summarize_quality(report['cases'])
            report.update(observed_summary)
            report['evaluation_valid']=(report['suite_still_frozen'] and report['implementation_unchanged']
                                        and not report.get('error_code'))
            if not report['evaluation_valid']:
                report['diagnostic_quality_summary']=observed_summary
                report['quality_pass_rate']=None
            report['blocked_prior_exposure']=sum(row.get('observation_status')=='blocked_prior_exposure' for row in report['cases'])
            report['provider_exposure_possible_cases']=sum(row.get('exposure_possible') is True for row in report['cases'])
            report['valid_model_responses']=sum(item.get('outcome')=='complete' for row in report['cases'] for item in row.get('model_receipts',[]))
            output=REPO/'evidence'/'acceptance_v3'/run_id/'report.json';output.parent.mkdir(parents=True)
            output.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2)+'\n')
        except (OSError,sqlite3.Error,StorageUnavailable,ValueError,TypeError):
            report_unavailable=True
    if report_unavailable:
        print(json.dumps({'blocked':'report_unavailable','run_id':run_id,'report_saved':False}))
        return 2
    summary={key:report[key] for key in ('quality_measured','quality_unmeasured','quality_passes','quality_pass_rate',
        'blocked_prior_exposure','provider_exposure_possible_cases','valid_model_responses','evaluation_valid')}
    summary['report']=str(output.relative_to(REPO))
    print(json.dumps(summary))
    if report['blocked_prior_exposure'] and not report['quality_measured']:return 2
    return 0 if (len(report['cases'])==len(cases) and all(row.get('quality_passed') is True for row in report['cases'])
                 and report['evaluation_valid']) else 1


if __name__=='__main__':sys.exit(main())
