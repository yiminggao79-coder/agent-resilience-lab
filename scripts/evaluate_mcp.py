#!/usr/bin/env python3
"""Public real-DeepSeek + owned synthetic local stdio MCP development experiment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from agent_lab.budget import BudgetLedger, BudgetedModel, load_first_line_key
from agent_lab.model import OpenAIModel, ModelError
from agent_lab.engine import Engine, Limits, public_status
from agent_lab.coding_tools import CodingTools
from agent_lab.registry import MCPToolRegistry
from agent_lab.store import Store
from agent_lab.sandbox import run_tests, observe_function
from agent_lab.tools import ToolError
from scripts.deepseek_live import RecordingModel, StreamMetadata, coding_cycle
from scripts.evaluate_acceptance import copy_project, digest_file, implementation_snapshot, json_equal
from scripts.local_mcp_fixture import TOOL, SERVER_NAME

TASK = ('Inspect this quote project. Discover and call the owned synthetic MCP fetch_policy tool for its current policy. '
        'Implement quote(amount) as amount * multiplier + offset from that policy, and store exactly its multiplier, offset '
        'and receipt fields in settings.json. Keep every original test byte unchanged; add new regression test files only. '
        'Read actual project files, make the changes, run the fixed run_tests tool successfully after your last change, '
        'and finish with evidence. Do not request general commands.')


def make_fixture(trial):
    trial = Path(trial)
    root, trusted = trial/'project', trial/'trusted'
    (root/'tests').mkdir(parents=True); trusted.mkdir(mode=0o700)
    (root/'quote.py').write_text('def quote(amount):\n    return amount\n')
    (root/'settings.json').write_text('{"receipt":"pending"}\n')
    (root/'tests'/'test_public.py').write_text(
        'import json\nimport unittest\nfrom pathlib import Path\nfrom quote import quote\n'
        'class Public(unittest.TestCase):\n    def test_configured(self):\n'
        '        self.assertNotEqual(json.loads(Path("settings.json").read_text())["receipt"], "pending")\n'
        '        self.assertIsInstance(quote(0), int)\n')
    expected = {'multiplier':secrets.randbelow(89)+11,'offset':secrets.randbelow(997)+1,'receipt':secrets.token_hex(24)}
    (trusted/'policy.json').write_text(json.dumps(expected))
    shutil.copyfile(REPO/'scripts'/'local_mcp_fixture.py', trusted/'server.py')
    spec = {'name':SERVER_NAME,'command':[str(Path(sys.executable).resolve()),'-I','-S',str(trusted/'server.py'),
            str(trusted/'policy.json'),str(trusted/'requests.jsonl')],'cwd':str(trusted.resolve()),
            'timeout':3,'max_bytes':8192,'max_pages':1,'max_tools':1}
    config = trusted/'mcp.json'; config.write_text(json.dumps({'servers':[spec]}))
    for path in trusted.iterdir(): os.chmod(path, 0o600)
    before = {'tests/test_public.py':digest_file(root/'tests'/'test_public.py')}
    return root, expected, before, config


def fixture_intent(registry):
    digest = hashlib.sha256(json.dumps(TOOL,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    tools = registry.manifest['tools']
    if len(tools) != 1 or tools[0]['server'] != SERVER_NAME or tools[0]['tool'] != TOOL['name'] or tools[0]['definition_sha256'] != digest:
        raise ValueError('fixture_catalog_mismatch')
    return registry.prepare(tools[0]['name'], {})


def authorized_intent(registry, intent, pinned):
    return intent == pinned and intent.get('args') == {} and fixture_intent(registry) == pinned


def grade_candidate(root, trial, expected, before, state, source_unchanged, protocol):
    result = {'task_success':False,'immutable_tests_unchanged':False,'oracle_matches':False,
              'receipt_settings_match':False,'new_regression_tests':False,'final_tests_exit_code':None,
              'confirmed_coding_cycle':coding_cycle(state['messages'])}
    if state['phase'] != 'completed' or state.get('outcome_unresolved') or not source_unchanged or not protocol:
        return result
    try:
        def immutable(): return all(digest_file(root/name) == want for name,want in before.items())
        result['immutable_tests_unchanged'] = immutable()
        if not result['immutable_tests_unchanged']: return result
        result['new_regression_tests'] = any(str(p.relative_to(root)) not in before for p in (root/'tests').rglob('test_*.py'))
        result['final_tests_exit_code'] = run_tests(root,12)['exit_code']
        result['immutable_tests_unchanged'] = immutable()
        candidate = Path(trial)/('candidate-'+uuid.uuid4().hex[:8]); copy_project(root,candidate)
        result['candidate_path'] = str(candidate)
        result['candidate_sha256'] = {str(p.relative_to(candidate)):digest_file(p) for p in sorted(candidate.rglob('*')) if p.is_file()}
        result['receipt_settings_match'] = json_equal(json.loads((candidate/'settings.json').read_text()),expected)
        inputs = [[-17],[0],[1],[53],[211]]
        observed = observe_function(candidate,'quote','quote',inputs,12)
        result['oracle_matches'] = observed['complete'] and json_equal(observed['observations'],
                                  [row[0]*expected['multiplier']+expected['offset'] for row in inputs])
        result['task_success'] = bool(all(result[k] for k in ('immutable_tests_unchanged','oracle_matches',
            'receipt_settings_match','new_regression_tests','confirmed_coding_cycle')) and result['final_tests_exit_code'] == 0)
    except (OSError, ValueError, ToolError):
        result['grading_error'] = 'candidate_verification_failed'
    return result


def run_trial(model, private, snapshot):
    root, expected, before, config = make_fixture(private)
    if run_tests(root,10)['exit_code'] == 0: raise ValueError('fixture_baseline_must_fail')
    trusted_hashes = {p:digest_file(p) for p in (config,config.parent/'server.py',config.parent/'policy.json')}
    with Store(private/'tasks.sqlite') as store, MCPToolRegistry(CodingTools(root),config) as registry:
        pinned = fixture_intent(registry)
        engine = Engine(store,registry,model)
        tid = engine.create(TASK,Limits(max_steps=32,max_seconds=600,model_timeout=90,tool_timeout=12,max_output_tokens=4096))
        state = engine.run(tid); approvals = 0
        while state['phase'] == 'awaiting_approval' and approvals < 2:
            if not all(digest_file(p) == want for p,want in trusted_hashes.items()): break
            if not authorized_intent(registry,state['pending']['intent'],pinned): break
            approval = engine.approval_request(tid)
            engine.decide_approval(tid,approval['request_id'],approval['digest'],True)
            approvals += 1; state = engine.run(tid)
        protocol = False
        for message in state['messages']:
            if message.get('role') != 'tool': continue
            payload = json.loads(message['content'])
            if payload.get('origin') == pinned['origin'] and payload.get('ok') is True:
                data = payload.get('structuredContent',{})
                protocol = json_equal(data.get('policy'),expected) and data.get('credential_env_present') == {
                    'DEEPSEEK_API_KEY':False,'OPENAI_API_KEY':False,'ANTHROPIC_API_KEY':False}
        unchanged = implementation_snapshot()['source_sha256'] == snapshot['source_sha256']
        judged = grade_candidate(root,private,expected,before,state,unchanged,protocol)
        return {**public_status(state),**judged,'task_id':tid,'actual_local_mcp_protocol':protocol,
                'implementation_unchanged':unchanged,'scoped_mcp_approvals':approvals,
                'manifest':registry.manifest,'trace':store.events(tid)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Explicitly enable one paid development attempt')
    parser.add_argument('--model',choices=('deepseek-flash','deepseek-v4-pro'),default='deepseek-flash')
    parser.add_argument('--stream',action='store_true')
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({'blocked':'live_flag_required','api_requests_sent':0})); return 2
    snapshot = implementation_snapshot()
    if snapshot['runtime_dirty']:
        print(json.dumps({'blocked':'commit_runtime_before_live_experiment','api_requests_sent':0})); return 2
    ledger = BudgetLedger(REPO/'.lab'/'paid-budget.sqlite')
    run_id = time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    private = REPO/'.lab'/'mcp-live'/run_id; private.mkdir(parents=True,mode=0o700)
    report = {'run_id':run_id,'kind':'public_real_llm_owned_synthetic_local_mcp_development',
              'implementation':snapshot,'model':args.model,'protocol':'chat','stream':args.stream,'thinking':'disabled',
              'cost_is_supplier_invoice':False,'independent_heldout':False,'server_label':'owned synthetic local stdio MCP service'}
    try:
        observer = StreamMetadata() if args.stream else None
        delegate = OpenAIModel(args.model,'https://api.deepseek.com','DEEPSEEK_API_KEY',True,
            protocol='chat',stream=args.stream,token_parameter='max_tokens',parallel_parameter=False,thinking='disabled',
            ca_bundle='/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').is_file() else None,
            on_text=observer.on_text if observer else None)
        model = RecordingModel(BudgetedModel(delegate,ledger),private,text_observer=observer)
        load_first_line_key()
        report['result'] = run_trial(model,private,snapshot)
    except (ModelError,ToolError) as error:
        report['error_code'] = error.code
    except Exception:
        report['error_code'] = 'mcp_experiment_internal_error'
    finally:
        os.environ.pop('DEEPSEEK_API_KEY',None)
        report['budget'] = ledger.report()
        report['model_requests'] = model.receipts if 'model' in locals() else []
        report['implementation_unchanged'] = implementation_snapshot()['source_sha256'] == snapshot['source_sha256']
        if 'result' in report: report['result']['task_success'] &= report['implementation_unchanged']
        output = REPO/'evidence'/'mcp-live'/(run_id+'.json'); output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    print(json.dumps({'report':str(output.relative_to(REPO)),'task_success':report.get('result',{}).get('task_success',False),
                      'error_code':report.get('error_code'),'committed_upper_cny':report['budget']['committed_upper_cny']}))
    return 0 if report.get('result',{}).get('task_success') and report['implementation_unchanged'] else 1


if __name__ == '__main__': sys.exit(main())
