"""Repeat real SIGKILL during write reconciliation; no production changes."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from probe_state import Engine, Fixture, Limits, Store, Tools, call, finish


def child(base, mode, tid=None):
    root = base / 'work'
    decisions = [finish()] if mode != 'create_kill' else [call('write_file', {'path': 'effect.txt', 'content': 'one\n'}), finish()]
    fixture = Fixture(decisions)
    def hook(event, state):
        if event == 'after_tool_effect' and mode.endswith('kill'):
            print(json.dumps({'task_id': state['id'], 'window': event,
                              'recovery_count': state['metrics']['recoveries']}), flush=True)
            os.kill(os.getpid(), signal.SIGKILL)
    with Store(base / 'private' / 'state.sqlite') as store:
        engine = Engine(store, Tools(root), fixture, hook)
        if mode == 'create_kill':
            tid = engine.create('Set effect.txt to one line', Limits(max_seconds=60))
        state = engine.run(tid)
        print(json.dumps({'task_id': tid, 'phase': state['phase'], 'reason': state['reason'],
                          'fixture_calls': fixture.calls, 'metrics': state['metrics']}))


def parent():
    with tempfile.TemporaryDirectory(prefix='arl-audit-repeat-') as raw:
        base = Path(raw); (base / 'work').mkdir()
        records = []; tid = None
        for mode in ('create_kill', 'recover_kill', 'recover_kill', 'recover', 'recover'):
            cmd = [sys.executable, str(Path(__file__).resolve()), '--child', str(base), mode]
            if tid: cmd.append(tid)
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            result = json.loads(p.stdout)
            tid = result['task_id']
            with Store(base / 'private' / 'state.sqlite') as store:
                state = store.load(tid)
                records.append({'mode': mode, 'returncode': p.returncode, 'output': result,
                                'durable_phase': state['phase'], 'pending': state['pending'],
                                'mtime_ns': (base / 'work' / 'effect.txt').stat().st_mtime_ns,
                                'file': (base / 'work' / 'effect.txt').read_text(),
                                'trace_kinds': [e['kind'] for e in store.events(tid)]})
        return records


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        child(Path(sys.argv[2]), sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    else:
        print(json.dumps(parent(), indent=2))
