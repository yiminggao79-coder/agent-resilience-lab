#!/usr/bin/env python3
"""Opt-in real Coding Agent milestone. No credentials => no API requests.

Independent file/test oracles decide success; model completion is insufficient.
This is a public development benchmark, not an unseen acceptance set.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits, public_status
from agent_lab.model import OpenAIModel
from agent_lab.store import Store
from agent_lab.sandbox import run_tests, observe_function
from agent_lab.tools import ToolError

CASES={
    'add':{'file':'calc.py','function':'add','inputs':[[2,3],[-3,8],[0,0],[1.5,2.5]],'expected':[5,5,0,4.0],
           'initial':'def add(a,b):\n    return a-b\n',
           'public':'import unittest\nfrom calc import add\nclass T(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2,3),5)\n',
           'oracle':'import unittest\nfrom calc import add\nclass T(unittest.TestCase):\n def test_values(self):\n  for a,b,w in [(2,3,5),(-3,8,5),(0,0,0),(1.5,2.5,4.0)]: self.assertEqual(add(a,b),w)\n',
           'task':'Inspect this Python project and fix the implementation bug without changing the public API or existing tests. Run the tests, use the actual feedback, and verify the saved implementation before finishing.'},
    'normalize':{'file':'names.py','function':'normalize','inputs':[['  Ada   LOVELACE  '],['A\tB'],[''],['  Alice ']],
                 'expected':['ada lovelace','a b','','alice'],'initial':'def normalize(value):\n    return value.strip()\n',
                 'public':'import unittest\nfrom names import normalize\nclass T(unittest.TestCase):\n def test_name(self): self.assertEqual(normalize("  Ada   LOVELACE  "),"ada lovelace")\n',
                 'oracle':'import unittest\nfrom names import normalize\nclass T(unittest.TestCase):\n def test_values(self):\n  for x,w in [("  Ada   LOVELACE  ","ada lovelace"),("A\\tB","a b"),("", ""),("  Alice ","alice")]: self.assertEqual(normalize(x),w)\n',
                 'task':'Inspect the project and fix normalize so that it trims surrounding whitespace, collapses internal whitespace to one space, and returns lower-case text. Preserve its API and existing tests. Run tests, use feedback, verify the final file.'}}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-api',action='store_true')
    p.add_argument('--model',default=os.environ.get('AGENT_LAB_MODEL'))
    p.add_argument('--base-url')
    p.add_argument('--api-key-env',default=None)
    p.add_argument('--protocol',choices=('chat','responses','anthropic'),default='chat')
    p.add_argument('--stream',action='store_true')
    p.add_argument('--token-parameter',choices=('max_tokens','max_completion_tokens'),default='max_completion_tokens')
    p.add_argument('--no-parallel-parameter',action='store_true')
    p.add_argument('--case',choices=tuple(CASES)+('all',),default='add')
    p.add_argument('--max-cost-usd',type=float,required=True,help='per task configured cost reservation limit')
    p.add_argument('--input-price',type=float,required=True)
    p.add_argument('--output-price',type=float,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    args.api_key_env=args.api_key_env or ('ANTHROPIC_API_KEY' if args.protocol=='anthropic' else 'OPENAI_API_KEY')
    if not args.allow_api or not args.model or not os.environ.get(args.api_key_env):
        p.error('requires explicit --allow-api, model, and configured key; never paste a key into task text')
    limits=Limits(max_steps=24,max_seconds=300,tool_timeout=12,max_output_tokens=4096,
                  max_cost_usd=args.max_cost_usd,input_price=args.input_price,output_price=args.output_price)
    model=OpenAIModel(args.model,args.base_url,args.api_key_env,True,protocol=args.protocol,stream=args.stream,
                      token_parameter=args.token_parameter,parallel_parameter=not args.no_parallel_parameter)
    rows=[]
    for name in (CASES if args.case=='all' else [args.case]):
        case=CASES[name]
        with tempfile.TemporaryDirectory(prefix='arl-real-coding-') as tmp:
            root=Path(tmp)/'project';(root/'tests').mkdir(parents=True)
            (root/case['file']).write_text(case['initial'])
            original_test=root/'tests'/'test_public.py';original_test.write_text(case['public'])
            original_hash=hashlib.sha256(original_test.read_bytes()).hexdigest()
            # Verify OS backend before spending any API budget.
            try:
                baseline=run_tests(root,10)
            except ToolError as error:
                print(json.dumps({'blocked':error.code,'api_requests_sent':0}))
                return 2
            if baseline['exit_code']==0:
                raise RuntimeError('invalid_benchmark_baseline')
            with Store(Path(tmp)/'private'/'tasks.sqlite') as store:
                engine=Engine(store,CodingTools(root),model)
                started=time.monotonic();tid=engine.create(case['task'],limits);state=engine.run(tid)
                unchanged=hashlib.sha256(original_test.read_bytes()).hexdigest()==original_hash if original_test.exists() else False
                source=(root/case['file']).read_bytes() if (root/case['file']).is_file() else b''
                oracle_root=Path(tmp)/'oracle';(oracle_root/'tests').mkdir(parents=True)
                (oracle_root/case['file']).write_bytes(source)
                # Candidate never receives expected values or owns judge assertions.
                oracle=observe_function(oracle_root,Path(case['file']).stem,case['function'],case['inputs'],10)
                observed=oracle['observations']
                oracle_matches=oracle['complete'] and all(
                    (type(actual)==type(expected) or type(actual) in (int,float) and type(expected) in (int,float))
                    and actual==expected for actual,expected in zip(observed,case['expected']))
                events=store.events(tid)
                confirmed_tools=[e['tool'] for e in events if e['kind']=='tool_finished' and e.get('ok')]
                required={'read_file','run_tests'}
                tool_cycle=required.issubset(confirmed_tools) and any(t in confirmed_tools for t in ('apply_patch','write_file'))
                success=state['phase']=='completed' and unchanged and oracle_matches and tool_cycle
                rows.append({'case':name,**public_status(state),'elapsed_seconds':time.monotonic()-started,
                             'public_tests_unchanged':unchanged,'independent_oracle_exit_code':oracle['exit_code'],
                             'independent_observations_complete':oracle['complete'],'independent_oracle_matches':oracle_matches,
                             'confirmed_coding_cycle':tool_cycle,'task_success':success,
                             'source_sha256':hashlib.sha256(source).hexdigest(),'trace':events})
        report={'kind':'real_llm_coding_development_benchmark','model':args.model,'protocol':args.protocol,
                'real_model_tested':True,'task_success_rate':sum(r['task_success'] for r in rows)/len(rows),
                'cases':rows,'recovery_success_rate':None,
                'limitations':['Public small Python tasks; not general Coding Agent competence.',
                               'No fault injection in this live suite; recovery rate unmeasured.',
                               'Cost from supplied prices/usage, not a supplier billing guarantee.']}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    print(json.dumps({'report':str(args.output),'task_success_rate':report['task_success_rate']}))
    return 0 if all(r['task_success'] for r in rows) else 1


if __name__=='__main__':sys.exit(main())
