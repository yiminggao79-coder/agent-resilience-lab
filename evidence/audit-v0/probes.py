"""Audit experiments only. No production edits, no real model or network.

Expectations are stated before execution; failures are reported, not repaired.
Run from repo: python3 evidence/audit-v0/probes.py
"""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from agent_lab.tools import Tools


def response(name=None, args=None, call_id='audit_call'):
    message = {'role': 'assistant', 'content': 'Finished'}
    if name:
        message = {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
    return {'message': message, 'usage': {'input_tokens': 101, 'output_tokens': 7}}


class Fixture:
    identity = 'audit_v0_offline_protocol'

    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def complete(self, messages, schemas, timeout, max_tokens):
        self.calls += 1
        return next(self.replies)


def setup(tmp, replies, limits=None):
    root = Path(tmp) / 'work'
    root.mkdir()
    store = Store(Path(tmp) / 'private' / 'state.sqlite')
    model = Fixture(replies)
    engine = Engine(store, Tools(root), model)
    tid = engine.create('Audit: append exactly once and finish.', limits or Limits())
    return root, store, model, engine, tid


def unknown_usage_invalid_message():
    """Safety expectation: missing usage with a cost cap pauses before any subsequent request."""
    malformed = {'message': {'role': 'assistant', 'content': None, 'tool_calls': []}, 'usage': None}
    with tempfile.TemporaryDirectory() as tmp:
        root, store, model, engine, tid = setup(tmp, [malformed, response()], Limits(max_cost_usd=1, input_price=1, output_price=1))
        try:
            state = engine.run(tid)
            return {'case': 'missing_usage_invalid_message', 'expectation': 'pause after first response, no second request',
                    'meets_expectation': model.calls == 1 and state['phase'] == 'needs_confirmation',
                    'actual_model_fixture_calls': model.calls, 'phase': state['phase'], 'reason': state['reason'],
                    'trace': store.events(tid)}
        finally:
            store.close()


def duplicate_call_identity():
    """Protocol expectation: replay of a committed call identity must not apply a second append."""
    append = response('append_file', {'path': 'receipt.txt', 'content': 'once\n'}, 'same_provider_identity')
    with tempfile.TemporaryDirectory() as tmp:
        root, store, model, engine, tid = setup(tmp, [append, append, response()])
        try:
            state = engine.run(tid)
            lines = (root / 'receipt.txt').read_text().splitlines()
            resumed = Engine(store, Tools(root), Fixture([])).run(tid)
            return {'case': 'duplicate_committed_call_identity', 'expectation': 'same call identity applies at most once',
                    'meets_expectation': len(lines) == 1, 'lines': len(lines), 'phase': state['phase'],
                    'terminal_resume_changed_file': (root / 'receipt.txt').read_text().splitlines() != lines,
                    'scope': 'This is a repeated model response, not proof of repeated recovery replay.',
                    'trace': store.events(tid)}
        finally:
            store.close()


def trace_accounting_gap():
    """Observability expectation: each completed response has attributable usage in the event stream."""
    with tempfile.TemporaryDirectory() as tmp:
        root, store, model, engine, tid = setup(tmp, [response('read_file', {'path': 9}), response()])
        try:
            state = engine.run(tid)
            events = store.events(tid)
            traced = sum(e.get('input_tokens') or 0 for e in events)
            total = state['metrics']['input_tokens']
            return {'case': 'invalid_arguments_trace_accounting', 'expectation': 'trace response usage sum equals checkpoint usage',
                    'meets_expectation': traced == total, 'checkpoint_input_tokens': total, 'trace_input_tokens': traced,
                    'source': 'synthetic usage only; no actual API accounting', 'trace': events}
        finally:
            store.close()


def alternating_failure_loop():
    """Progress expectation: distinguish recovery from repeatedly alternating the same failure and no-op."""
    sequence = [response('read_file', {'path': 'missing'}), response('list_files', {})] * 4
    with tempfile.TemporaryDirectory() as tmp:
        root, store, model, engine, tid = setup(tmp, sequence, Limits(max_steps=8, max_errors=2))
        try:
            state = engine.run(tid)
            return {'case': 'alternating_failure_and_success_loop',
                    'expectation': 'recognize a repeated no-progress pattern before exhausting all steps',
                    'meets_expectation': model.calls < 8, 'fixture_calls': model.calls,
                    'phase': state['phase'], 'reason': state['reason'], 'consecutive_errors': state['errors'],
                    'scope': 'No unbounded loop: max_steps still enforces a hard request-count bound.',
                    'trace': store.events(tid)}
        finally:
            store.close()


def child_orphan(root, database):
    model = Fixture([response('append_file', {'path': 'late.txt', 'content': 'late\n'}), response()])
    with Store(database) as store:
        engine = Engine(store, Tools(root), model)
        tid = engine.create('Audit late worker', Limits(max_seconds=30, tool_timeout=20))
        original = subprocess.Popen
        stopped = [False]

        def intercept(*args, **kwargs):
            proc = original(*args, **kwargs)
            command = args[0] if args else kwargs.get('args', [])
            # After prepare succeeded, stop the real production execution worker.
            durable = store.load(tid)
            if not stopped[0] and command and str(command[-1]).endswith('/agent_lab/tools.py') and durable['phase'] == 'tool_pending' and durable['pending']['status'] == 'running':
                stopped[0] = True
                os.kill(proc.pid, signal.SIGSTOP)
                print(json.dumps({'runner': os.getpid(), 'worker': proc.pid, 'task': tid}), flush=True)
            return proc

        with patch('subprocess.Popen', intercept):
            engine.run(tid)


def orphan_after_kill():
    """Cancellation expectation: an old tool cannot create effects after a new runner has resumed/paused."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'work'
        root.mkdir()
        database = Path(tmp) / 'private' / 'state.sqlite'
        runner = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--orphan-child', str(root), str(database)],
                                  cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                  start_new_session=True)
        worker = None
        try:
            with selectors.DefaultSelector() as sel:
                sel.register(runner.stdout, selectors.EVENT_READ)
                if not sel.select(8):
                    raise RuntimeError('worker announcement timeout')
            notice = json.loads(runner.stdout.readline())
            worker = notice['worker']
            # SIGSTOP is the scheduling injection; allow the parent to fill the real worker stdin pipe.
            time.sleep(.2)
            os.kill(runner.pid, signal.SIGKILL)
            runner.wait(timeout=5)
            with Store(database) as store:
                resumed = Engine(store, Tools(root), Fixture([])).run(notice['task'])
                absent_when_resumed = not (root / 'late.txt').exists()
                os.kill(worker, signal.SIGCONT)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not (root / 'late.txt').exists():
                    time.sleep(.02)
                exists_later = (root / 'late.txt').exists()
                return {'case': 'orphan_tool_after_runner_sigkill',
                        'expectation': 'old worker cannot produce later effects after runner restart',
                        'meets_expectation': not exists_later, 'real_runner_exit_code': runner.returncode,
                        'file_absent_at_resume': absent_when_resumed, 'file_appeared_after_old_worker_continued': exists_later,
                        'resumed_phase': resumed['phase'], 'stored_phase_after_effect': store.load(notice['task'])['phase'],
                        'injection': 'Popen observer stops real tools.py execute worker before stdin is consumed; parent receives SIGKILL; no production code changed',
                        'trace': store.events(notice['task'])}
        finally:
            if runner.poll() is None:
                runner.kill()
                runner.wait(timeout=5)
            if worker:
                try:
                    os.kill(worker, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            runner.stdout.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--orphan-child':
        child_orphan(sys.argv[2], sys.argv[3])
        return
    results = []
    for probe in (unknown_usage_invalid_message, duplicate_call_identity, trace_accounting_gap, alternating_failure_loop, orphan_after_kill):
        try:
            results.append(probe())
        except Exception as exc:
            results.append({'case': probe.__name__, 'experiment_error': type(exc).__name__, 'message': str(exc)})
    destination = Path(__file__).with_name('primary-probes.json')
    destination.write_text(json.dumps({'kind': 'adversarial_offline_harness_audit', 'real_model_tested': False, 'results': results}, indent=2) + '\n')
    for result in results:
        print(json.dumps({k: v for k, v in result.items() if k != 'trace'}))


if __name__ == '__main__':
    main()
