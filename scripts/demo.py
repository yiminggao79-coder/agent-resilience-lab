#!/usr/bin/env python3
"""Reproducible OFFLINE harness demonstration, not an autonomous LLM benchmark."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_lab.engine import Engine, public_status
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel, tool, final
from tests.dev.test_crash import start_and_kill, restart


def crash_comparison():
    rows = []
    for operation in ('write_file', 'append_file'):
        for strategy in ('resume', 'restart_without_checkpoint'):
            with tempfile.TemporaryDirectory(prefix='arl-experiment-') as tmp:
                root = Path(tmp) / 'work'
                root.mkdir()
                database = Path(tmp) / 'state' / 'db.sqlite'
                started = time.monotonic()
                first_id = start_and_kill(root, database, operation)
                with Store(database) as store:
                    first = store.load(first_id)
                    before_trace = store.events(first_id)
                result = restart(root, database, operation, 'resume' if strategy == 'resume' else 'fresh')
                text = (root / 'result.txt').read_text()
                with Store(database) as store:
                    trace = store.events(result['id'])
                rows.append({'operation': operation, 'strategy': strategy, 'phase': result['phase'],
                             'reason': result['reason'], 'task_success': result['phase'] == 'completed' and text == 'one\n',
                             'target_matches': text == 'one\n', 'line_count': len(text.splitlines()),
                             'elapsed_seconds': time.monotonic() - started,
                             'model_calls_total': result['metrics']['model_calls'] + (first['metrics']['model_calls'] if strategy != 'resume' else 0),
                             'tool_attempts_total': result['metrics']['tool_attempts'] + (first['metrics']['tool_attempts'] if strategy != 'resume' else 0),
                             'actual_kill_signal': 'SIGKILL', 'crash_stage': 'after_tool_effect_before_receipt',
                             'trace_before_kill': before_trace, 'trace_after_restart': trace})
    return rows


def run_demo():
    with tempfile.TemporaryDirectory(prefix='arl-demo-') as tmp:
        root = Path(tmp) / 'work'
        root.mkdir()
        (root / 'input.csv').write_text('name,score\na,2\nb,8\nc,5\n')
        # These are deliberately fixed protocol responses. They test the harness, not model reasoning.
        model = ScriptedModel([tool('read_file', {'path': 'input.csv'}),
                               tool('csv_stats', {'path': 'input.csv', 'column': 'score'}),
                               tool('python_expr', {'expression': '15 / 3'}),
                               tool('write_file', {'path': 'report.json', 'content': '{"count":3,"sum":15,"mean":5}'}),
                               tool('read_file', {'path': 'report.json'}), final('Offline protocol fixture finished')])
        with Store(Path(tmp) / 'state' / 'db.sqlite') as store:
            engine = Engine(store, Tools(root), model)
            tid = engine.create('OFFLINE FIXTURE: summarize input.csv into report.json')
            state = engine.run(tid)
            accepted = json.loads((root / 'report.json').read_text()) == {'count': 3, 'sum': 15, 'mean': 5}
            output = {'kind': 'offline_protocol_fixture', 'real_model_tested': False,
                      'real_agent_success_rate': None, 'real_api_cost_usd': None,
                      'normal_case': {**public_status(state), 'artifact_verified': accepted, 'trace': store.events(tid)},
                      'crash_comparison': crash_comparison(),
                      'limitations': ['Scripted responses; no model capability claim.',
                                      'One synthetic run per case; no statistical significance.',
                                      'No-checkpoint baseline restarts the same scripted policy from the start.',
                                      'tool_attempts counts durable start records, not guaranteed external execution counts.',
                                      'Usage in protocol fixtures is synthetic; excluded from real API metrics.']}
            return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = run_demo()
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n')
    print(text)
    return 0 if result['normal_case']['artifact_verified'] else 1


if __name__ == '__main__':
    sys.exit(main())
