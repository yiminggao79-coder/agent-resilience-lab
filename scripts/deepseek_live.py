#!/usr/bin/env python3
"""Authorized DeepSeek experiments. Key is read from one line, never an argument.

All commands share the project's persistent CNY 30 ledger. Do not delete/reset
that ledger to continue experiments. Artifacts contain synthetic projects only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from agent_lab.budget import BudgetLedger, BudgetedModel, load_first_line_key
from agent_lab.model import OpenAIModel, ModelError, normalize_diagnostics
from agent_lab.engine import Engine, Limits, public_status
from agent_lab.coding_tools import CodingTools
from agent_lab.store import Store
from agent_lab.sandbox import run_tests, observe_function
from agent_lab.tools import ToolError
from scripts.evaluate_coding import CASES


class StreamMetadata:
    """Observe private text without putting text in public experiment evidence."""
    def begin(self):
        self.started=time.monotonic()
        self.chunks=self.byte_count=0
        self.first_delta=None
        self.digest=hashlib.sha256()

    def on_text(self,text):
        if self.first_delta is None:self.first_delta=time.monotonic()-self.started
        raw=text.encode('utf-8');self.digest.update(raw)
        self.chunks+=1;self.byte_count+=len(raw)

    def finish(self,result):
        elapsed=time.monotonic()-self.started
        text=result['message'].get('content') or '' if result else None
        return {'chunks':self.chunks,'utf8_bytes':self.byte_count,'sha256':self.digest.hexdigest(),
                'first_delta_seconds':self.first_delta,
                'completion_seconds':elapsed,
                'first_delta_before_return':self.first_delta is not None and self.first_delta<elapsed,
                'matches_completed_content':bool(result is not None and
                    self.digest.hexdigest()==hashlib.sha256(text.encode('utf-8')).hexdigest())}


class RecordingModel:
    """Private request/response receipts; public summaries contain metadata only."""
    def __init__(self, delegate, private, text_observer=None):
        self.delegate, self.private, self.identity = delegate, Path(private), delegate.identity
        self.receipts = []
        self.text_observer=text_observer

    def _write(self, name, value):
        target = self.private/name
        target.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
        os.chmod(target, 0o600)

    def complete(self, messages, tools, timeout, max_tokens):
        index = len(self.receipts) + 1
        prefix = 'model-%03d-' % index
        self._write(prefix+'request.json', {'messages': messages, 'tools': tools, 'max_tokens': max_tokens})
        started = time.monotonic()
        receipt = {'index': index}
        result=None
        if self.text_observer:self.text_observer.begin()
        try:
            result = self.delegate.complete(messages, tools, timeout, max_tokens)
        except ModelError as error:
            receipt.update({'outcome': 'error', 'error_code': error.code, 'http_status': error.http_status})
            receipt['diagnostic'] = normalize_diagnostics(error.diagnostic)
            if error.response_body_bytes is not None:
                receipt['response_body_bytes'] = error.response_body_bytes
            self._write(prefix+'error.json', receipt)
            raise
        else:
            self._write(prefix+'response.json', result)
            receipt.update({'outcome': 'complete', 'usage': result.get('usage'),
                            'finish_reason': result.get('finish_reason'),
                            'tools': [call['function']['name'] for call in result['message'].get('tool_calls', [])]})
            return result
        finally:
            receipt['elapsed_seconds'] = time.monotonic()-started
            if self.text_observer:receipt['text_stream']=self.text_observer.finish(result)
            self.receipts.append(receipt)


def make_case(root, name):
    case = dict(CASES[name])
    root.mkdir(parents=True, exist_ok=True)
    (root/'tests').mkdir(exist_ok=True)
    (root/case['file']).write_text(case['initial'])
    test = root/'tests'/'test_public.py'
    test.write_text(case['public'])
    case['test_hash'] = hashlib.sha256(test.read_bytes()).hexdigest()
    return case


def check_case(root, case, observer=observe_function):
    test = root/'tests'/'test_public.py'
    unchanged = test.is_file() and hashlib.sha256(test.read_bytes()).hexdigest() == case['test_hash']
    source = root/case['file']
    oracle_root = root.parent / ('oracle_' + uuid.uuid4().hex)
    oracle_root.mkdir()
    try:
        if source.is_file() and not source.is_symlink():
            shutil.copyfile(source, oracle_root/case['file'])
        observed = observer(oracle_root, Path(case['file']).stem, case['function'], case['inputs'], 10)
        values = observed['observations']
        matches = observed['complete'] and len(values) == len(case['expected']) and all(
            ((type(a) == type(b)) or (type(a) in (int, float) and type(b) in (int, float))) and a == b
            for a, b in zip(values, case['expected']))
        return {'public_tests_unchanged': unchanged, 'oracle_matches': bool(matches),
                'observations_complete': observed['complete'], 'oracle_exit_code': observed['exit_code']}
    finally:
        shutil.rmtree(oracle_root)


def probe(model, private):
    tools = [{'type': 'function', 'function': {'name': 'add_numbers',
              'description': 'Compute the sum of two integers.', 'parameters': {'type': 'object',
              'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}},
              'required': ['a', 'b'], 'additionalProperties': False}}}]
    messages = [{'role': 'user', 'content': 'Use add_numbers to compute 1337 + 29, then report its returned sum and exact receipt string.'}]
    first = model.complete(messages, tools, 90, 2048)
    (private/'probe-first.json').write_text(json.dumps(first))
    os.chmod(private/'probe-first.json', 0o600)
    calls = first['message'].get('tool_calls', [])
    if len(calls) != 1 or calls[0]['function']['name'] != 'add_numbers':
        return {'tool_selection': False, 'first_usage': first['usage']}
    args = json.loads(calls[0]['function']['arguments'])
    if set(args) != {'a', 'b'} or any(type(v) is not int or abs(v) > 10000 for v in args.values()):
        return {'tool_selection': True, 'arguments_valid': False, 'first_usage': first['usage']}
    result = args['a'] + args['b']
    receipt = uuid.uuid4().hex
    messages += [first['message'], {'role': 'tool', 'tool_call_id': calls[0]['id'], 'content': json.dumps({'sum': result, 'receipt': receipt})}]
    try:
        second = model.complete(messages, tools, 90, 2048)
    except ModelError as error:
        return {'tool_selection': True, 'arguments_valid': True, 'actual_sum': result,
                'feedback_used': False, 'feedback_error': error.code, 'feedback_http_status': error.http_status,
                'first_usage': first['usage'], 'first_finish': first['finish_reason']}
    (private/'probe-second.json').write_text(json.dumps(second))
    os.chmod(private/'probe-second.json', 0o600)
    content = second['message'].get('content') or ''
    return {'tool_selection': True, 'arguments_valid': True, 'actual_sum': result,
            'feedback_used': result == 1366 and '1366' in content and receipt in content and not second['message'].get('tool_calls') and second['finish_reason'] in ('stop', 'end_turn', 'completed'),
            'first_usage': first['usage'], 'second_usage': second['usage'],
            'first_finish': first['finish_reason'], 'second_finish': second['finish_reason'],
            'private_reasoning_roundtrip': bool(first['message'].get('reasoning_content')) or any(b.get('type') in ('thinking','redacted_thinking') for b in first['message'].get('native_content', []))}


def coding_cycle(messages):
    """Judge actual saved sequence, including test exit status after the last edit."""
    read_at, edited_at, tested_at = -1, -1, -1
    calls = {}
    for index, message in enumerate(messages):
        if message['role'] == 'assistant':
            for call in message.get('tool_calls', []):
                calls[call['id']] = call['function']['name']
        elif message['role'] == 'tool':
            name = calls.get(message['tool_call_id'])
            # Any newer test attempt supersedes earlier evidence, including a
            # failed, timed-out or malformed result. Mutators can have effects
            # even when their feedback cannot be decoded.
            if name == 'run_tests':
                tested_at = -1
            if name in ('apply_patch','apply_patch_text','edit_file','write_file','append_file','delete_file','create_directory','run_command') or (isinstance(name,str) and name.startswith('mcp_')):
                if read_at >= 0:
                    edited_at = index
            try:
                payload = json.loads(message['content'])
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get('ok') is not True:
                continue
            if name in ('read_file', 'read_lines'):
                read_at = index
            elif name in ('apply_patch', 'apply_patch_text', 'edit_file', 'write_file') and read_at >= 0:
                edited_at = index
            elif name == 'run_tests' and type(payload.get('exit_code')) is int and payload['exit_code'] == 0:
                tested_at = index
    return 0 <= edited_at < tested_at


def coding(model, root, name):
    case = make_case(root, name)
    baseline = run_tests(root, 10)
    if baseline['exit_code'] == 0:
        raise ValueError('benchmark_baseline_must_fail')
    limits = Limits(max_steps=32, max_seconds=600, model_timeout=90, tool_timeout=12, max_output_tokens=4096)
    with Store(root.parent/'tasks.sqlite') as store:
        def progress(event):
            print(json.dumps({k: event[k] for k in ('kind', 'phase', 'tool', 'code') if k in event}), flush=True)
        engine = Engine(store, CodingTools(root), model, observer=progress)
        tid = engine.create(case['task'], limits)
        started = time.monotonic()
        state = engine.run(tid)
        judged = check_case(root, case)
        events = store.events(tid)
        used = [e['tool'] for e in events if e['kind'] == 'tool_finished' and e.get('ok')]
        cycle = coding_cycle(state['messages'])
        success = state['phase'] == 'completed' and judged['public_tests_unchanged'] and judged['oracle_matches'] and cycle
        return {'case': name, 'task_id': tid, **public_status(state), **judged, 'tools': used,
                'confirmed_coding_cycle': cycle, 'task_success': bool(success),
                'elapsed_seconds': time.monotonic()-started, 'trace': events,
                'limitations': ['Public tiny development task; not unseen acceptance or general competence.',
                                'Model completion alone is insufficient; external value oracle required.',
                                'No recovery injection in this live run.']}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('budget', 'probe', 'coding'))
    p.add_argument('--model', choices=('deepseek-flash', 'deepseek-v4-pro'), default='deepseek-flash')
    p.add_argument('--protocol', choices=('chat', 'anthropic', 'responses'), default='chat')
    p.add_argument('--thinking', choices=('enabled', 'disabled', 'adaptive'), default='disabled')
    p.add_argument('--stream', action='store_true')
    p.add_argument('--reasoning-effort', choices=('low','high','max'), default=None)
    p.add_argument('--case', choices=tuple(CASES), default='add')
    args = p.parse_args(argv)
    ledger = BudgetLedger(REPO/'.lab'/'paid-budget.sqlite')
    if args.command == 'budget':
        print(json.dumps(ledger.report(), indent=2))
        return 0
    run_id = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    private = REPO/'.lab'/'live'/run_id
    private.mkdir(parents=True, mode=0o700)
    os.chmod(private, 0o700)
    output = REPO/'evidence'/'live'/(run_id+'.json')
    report = {'run_id': run_id, 'kind': 'official_api_protocol_probe' if args.command == 'probe' else 'real_llm_coding_development_task',
              'model': args.model, 'protocol': args.protocol, 'stream': args.stream, 'thinking': args.thinking if args.protocol != 'responses' else 'provider_default',
              'cost_is_supplier_invoice': False, 'reasoning_effort': args.reasoning_effort}
    started = time.monotonic()
    try:
        base = 'https://api.deepseek.com/anthropic' if args.protocol == 'anthropic' else 'https://api.deepseek.com'
        text_observer=StreamMetadata() if args.stream else None
        delegate = OpenAIModel(args.model, base, 'DEEPSEEK_API_KEY', True, protocol=args.protocol,
                               stream=args.stream, token_parameter='max_tokens', parallel_parameter=False,
                               thinking=None if args.protocol == 'responses' else args.thinking, reasoning_effort=args.reasoning_effort,
                               ca_bundle='/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').is_file() else None,
                               on_text=text_observer.on_text if text_observer else None)
        model = RecordingModel(BudgetedModel(delegate, ledger), private,text_observer=text_observer)
        load_first_line_key()
        report['result'] = probe(model, private) if args.command == 'probe' else coding(model, private/'project', args.case)
    except (ModelError, ToolError) as error:
        report['error_code'] = error.code
        if isinstance(error, ModelError):
            report['http_status'] = error.http_status
            report['diagnostic'] = error.diagnostic
    except Exception:
        report['error_code'] = 'experiment_internal_error'
        # No exception payloads may enter public artifacts.
    finally:
        os.environ.pop('DEEPSEEK_API_KEY', None)
        report['elapsed_seconds'] = time.monotonic()-started
        report['budget'] = ledger.report()
        report['model_requests'] = model.receipts if 'model' in locals() else []
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
    print(json.dumps({'report': str(output.relative_to(REPO)), 'error_code': report.get('error_code'),
                      'result': {k: v for k, v in report.get('result', {}).items() if k not in ('trace', 'metrics')},
                      'committed_upper_cny': report['budget']['committed_upper_cny']}, ensure_ascii=False))
    passed = report.get('result', {}).get('task_success' if args.command == 'coding' else 'feedback_used', False)
    return 0 if 'error_code' not in report and passed else 1


if __name__ == '__main__':
    sys.exit(main())
