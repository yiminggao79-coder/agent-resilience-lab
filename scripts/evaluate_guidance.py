#!/usr/bin/env python3
"""Public real-model development experiment for scoped rules and skill disclosure.

Not a heldout benchmark. Uses only the authorized DeepSeek route and shared cap.
"""
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
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits,public_status
from agent_lab.model import OpenAIModel,ModelError
from agent_lab.sandbox import run_tests,observe_function
from agent_lab.store import Store
from agent_lab.tools import ToolError
from scripts.deepseek_live import RecordingModel,StreamMetadata,coding_cycle
from scripts.evaluate_acceptance import copy_project,digest_file,json_equal,implementation_snapshot


def make_fixture(root,canary):
    root=Path(root);root.mkdir(parents=True)
    files={
        'README.md':'Fix canonical_keys in labelkit/normalize.py. Read scoped project guidance. Preserve existing tests and add regressions as useful. The public contract lives in the scoped rules.\n',
        'AGENTS.md':'Before changing a file, consult applicable scoped project rules. Discover relevant project skills and load the applicable testing procedure. Existing tests must remain unchanged.\n',
        'labelkit/AGENTS.md':'For canonical_keys(values): trim Unicode whitespace, casefold (not lower) each string, discard empty results, deduplicate canonical results preserving first occurrence order. Return a new list and do not change the input.\n',
        'labelkit/__init__.py':'',
        'labelkit/normalize.py':'def canonical_keys(values):\n    return sorted(set(value.lower() for value in values))\n',
        'normalizer.py':'from labelkit.normalize import canonical_keys\n\ndef normalize(values):\n    return canonical_keys(values)\n',
        'tests/__init__.py':'',
        'tests/test_public.py':"import unittest\nfrom normalizer import normalize\n\nclass PublicTests(unittest.TestCase):\n    def test_whitespace_and_order(self):\n        values=[' B ', 'a', 'b', ' ']\n        self.assertEqual(normalize(values), ['b','a'])\n        self.assertEqual(values, [' B ', 'a', 'b', ' '])\n",
        '.agents/skills/regression-check/SKILL.md':
            '---\nname: regression-check\ndescription: Testing procedure for normalization bug fixes; load when fixing canonical key handling.\n---\n'
            'Read the baseline test failure before editing. Add a Unicode regression without changing original tests. '
            'After all edits, run the fixed test tool again. The skill audit receipt is '+canary+'. Include that exact receipt in the final answer so the experiment can establish the body was read.\n',
    }
    for relative,content in files.items():
        path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
    return {'task':'Read README.md and fix the normalization bug according to applicable project guidance. Discover and use the relevant project skill. Keep existing tests unchanged, add useful regressions, run tests and verify the result.',
            'canary':canary,'immutable':{name:digest_file(root/name) for name in files if name.startswith('tests/')},
            'inputs':[[[' Straße ','STRASSE','ß',' SS ','','\t']], [[' Σ ','ς','σ',' A ','a',' B ']], [[]]],
            'expected':[['strasse','ss'],['σ','a','b'],[]],
            'fixture_sha256':{name:hashlib.sha256(content.encode()).hexdigest() for name,content in files.items()}}


def guidance_evidence(messages,canary):
    calls={};loaded=scope=False
    for message in messages:
        for call in message.get('tool_calls',[]):calls[call['id']]=call['function']
        if message.get('role')!='tool':continue
        try:
            result=json.loads(message['content']);function=calls[message['tool_call_id']]
            args=json.loads(function['arguments'])
        except (KeyError,ValueError,TypeError):continue
        if not isinstance(result,dict) or result.get('ok') is not True:continue
        if function['name']=='load_skill' and args.get('name')=='regression-check':loaded=True
        if function['name']=='project_rules' and args.get('path','').startswith('labelkit/'):scope=True
    final=messages[-1] if messages else {}
    used=final.get('role')=='assistant' and canary in (final.get('content') or '')
    return {'skill_loaded':loaded,'scope_rules_read':scope,'skill_receipt_in_final':used,
            'guidance_verified':bool(loaded and scope and used)}


def main():
    snapshot=implementation_snapshot()
    if snapshot['runtime_dirty']:
        print(json.dumps({'blocked':'commit_runtime_before_experiment','api_requests_sent':0}));return 2
    run_id=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    private=REPO/'.lab/guidance'/run_id;private.mkdir(parents=True,mode=0o700)
    receipts=private/'receipts';receipts.mkdir(mode=0o700)
    root=private/'project';case=make_fixture(root,'GUIDANCE-'+uuid.uuid4().hex)
    ledger=BudgetLedger(REPO/'.lab/paid-budget.sqlite')
    report={'kind':'real_project_guidance_development_experiment','run_id':run_id,'implementation':snapshot,
            'fixture_sha256':case['fixture_sha256'],'model':'deepseek-flash','protocol':'chat',
            'limitations':['Public development experiment, not independent acceptance.','One task cannot establish general instruction obedience.','Deterministic context views, not semantic summaries.']}
    started=time.monotonic()
    try:
        baseline=run_tests(root,12)
        if baseline['exit_code']==0:raise ValueError('fixture_baseline_must_fail')
        observer=StreamMetadata()
        delegate=OpenAIModel('deepseek-flash','https://api.deepseek.com','DEEPSEEK_API_KEY',True,
                             stream=True,token_parameter='max_tokens',parallel_parameter=False,
                             thinking='disabled',ca_bundle='/etc/ssl/cert.pem',on_text=observer.on_text)
        model=RecordingModel(BudgetedModel(delegate,ledger),receipts,text_observer=observer)
        load_first_line_key()
        with Store(private/'tasks.sqlite') as store:
            engine=Engine(store,CodingTools(root),model,observer=lambda e:print(json.dumps({k:e[k] for k in ('kind','phase','tool','code') if k in e}),flush=True))
            tid=engine.create(case['task'],Limits(max_steps=40,max_seconds=600,model_timeout=90,tool_timeout=12,max_output_tokens=4096))
            initial=store.load(tid)['messages']
            report['skill_body_absent_initially']=case['canary'] not in json.dumps(initial)
            report['skill_catalog_present_initially']='regression-check' in json.dumps(initial)
            state=engine.run(tid);approvals=0
            while state['phase']=='awaiting_approval':
                if state['pending']['intent'].get('name')!='run_command':break
                from agent_lab.commands import validate_command
                validate_command(root,state['pending']['intent'])
                approval=engine.approval_request(tid)
                engine.decide_approval(tid,approval['request_id'],approval['digest'],True)
                approvals+=1;state=engine.run(tid)
            unchanged=matches=final_code=None
            if state['phase']=='completed':
                unchanged=all(digest_file(root/name)==want for name,want in case['immutable'].items())
                if unchanged:
                    final_code=run_tests(root,12)['exit_code']
                    unchanged=all(digest_file(root/name)==want for name,want in case['immutable'].items())
                oracle_root=private/'oracle';copy_project(root,oracle_root)
                result=observe_function(oracle_root,'normalizer','normalize',case['inputs'],12)
                matches=result['complete'] and json_equal(result['observations'],case['expected'])
            proof=guidance_evidence(state['messages'],case['canary'])
            report['result']={**public_status(state),**proof,'original_tests_unchanged':unchanged,
                  'final_public_tests_exit_code':final_code,'oracle_matches':matches,
                  'confirmed_coding_cycle':coding_cycle(state['messages']),'sandbox_commands_preapproved':approvals}
            report['result']['task_success']=bool(state['phase']=='completed' and unchanged and matches and final_code==0 and
                  proof['guidance_verified'] and report['skill_body_absent_initially'] and coding_cycle(state['messages']))
            report['trace']=store.events(tid)
        report['model_requests']=model.receipts
    except (ModelError,ToolError) as error:report['error_code']=error.code
    except Exception:report['error_code']='experiment_internal_error'
    finally:
        os.environ.pop('DEEPSEEK_API_KEY',None)
        report['elapsed_seconds']=time.monotonic()-started
        report['budget']=ledger.report()
        report['model_requests']=model.receipts if 'model' in locals() else []
        report['implementation_unchanged']=implementation_snapshot()['source_sha256']==snapshot['source_sha256']
        output=REPO/'evidence/guidance'/run_id/'report.json';output.parent.mkdir(parents=True)
        report['artifact_sha256']={}
        for path in root.rglob('*'):
            if not path.is_file() or '__pycache__' in path.parts or path.name.startswith('.agent'):continue
            relative=path.relative_to(root)
            # Only generated fixture files and visible model outputs; no control/checkpoint files.
            if any(p.startswith('.') for p in relative.parts) and str(relative) not in case['fixture_sha256']:continue
            if path.is_symlink() or path.stat().st_size>65536:continue
            target=output.parent/'candidate'/relative;target.parent.mkdir(parents=True,exist_ok=True)
            raw=path.read_bytes();target.write_bytes(raw)
            report['artifact_sha256'][str(relative)]=hashlib.sha256(raw).hexdigest()
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'report':str(output.relative_to(REPO)),'result':report.get('result'),
                     'error_code':report.get('error_code'),'committed_upper_cny':report['budget']['committed_upper_cny']}))
    return 0 if report.get('result',{}).get('task_success') and report['implementation_unchanged'] else 1


if __name__=='__main__':sys.exit(main())
