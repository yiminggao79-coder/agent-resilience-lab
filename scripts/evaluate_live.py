#!/usr/bin/env python3
"""Opt-in real model evaluation with file-based oracles. Does not run on import."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_lab.engine import Engine, Limits, public_status
from agent_lab.model import OpenAIModel
from agent_lab.store import Store
from agent_lab.tools import Tools


# Development benchmark, intentionally separate from independent acceptance.
# Expected values are only used by the evaluator, never sent to the harness.
CASES = [
    {'name': 'csv_report', 'input': 'value\n3\n9\n12\n', 'file': 'data.csv',
     'task': 'Read data.csv and compute count, sum and mean of the value column. Save a JSON object with keys count, sum, mean in report.json, then read it back to verify.',
     'output': 'report.json', 'expected': {'count': 3, 'sum': 24, 'mean': 8}},
    {'name': 'negative_decimal', 'input': 'amount\n-2.5\n7.5\n0\n', 'file': 'data.csv',
     'task': 'Analyze amount in data.csv. Write report.json as JSON with keys count, sum, min, max. Verify the saved artifact with tools before finishing.',
     'output': 'report.json', 'expected': {'count': 3, 'sum': 5, 'min': -2.5, 'max': 7.5}},
    {'name': 'inspect_then_repair', 'input': 'value\n4\n6\n', 'file': 'actual.csv',
     'task': 'A previous note says the data file is missing.csv, but that note might be wrong. Inspect the workspace and summarize the actual CSV value column into report.json with count and sum. Verify the output.',
     'output': 'report.json', 'expected': {'count': 2, 'sum': 10}},
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-api', action='store_true')
    parser.add_argument('--model', default=os.environ.get('AGENT_LAB_MODEL'))
    parser.add_argument('--base-url')
    parser.add_argument('--api-key-env', default='OPENAI_API_KEY')
    parser.add_argument('--max-cost-usd', type=float, required=True, help='per task estimated cap; entire suite may reserve 3x this value')
    parser.add_argument('--input-price', type=float, required=True, help='user-verified USD per million input tokens')
    parser.add_argument('--output-price', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_api or not os.environ.get(args.api_key_env) or not args.model:
        parser.error('requires --allow-api, --model and a configured API key environment variable')
    limits = Limits(max_steps=12, max_seconds=120, max_cost_usd=args.max_cost_usd,
                    input_price=args.input_price, output_price=args.output_price)
    model = OpenAIModel(args.model, args.base_url, args.api_key_env, True)
    rows = []
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix='arl-live-') as tmp:
            root = Path(tmp) / 'work'
            root.mkdir()
            (root / case['file']).write_text(case['input'])
            with Store(Path(tmp) / 'state' / 'db.sqlite') as store:
                engine = Engine(store, Tools(root), model)
                started = time.monotonic()
                tid = engine.create(case['task'], limits)
                state = engine.run(tid)
                verified = False
                try:
                    verified = json.loads((root / case['output']).read_text()) == case['expected']
                except (OSError, ValueError):
                    pass
                rows.append({'case': case['name'], **public_status(state), 'artifact_verified': verified,
                             'task_success': verified and state['phase'] == 'completed',
                             'elapsed_seconds': time.monotonic() - started, 'trace': store.events(tid)})
        # Flush after every case so an interrupted evaluation preserves completed observations.
        result = {'kind': 'live_model_development_benchmark', 'model': args.model,
                  'configured_prices_usd_per_million': {'input': args.input_price, 'output': args.output_price},
                  'completed_cases': len(rows), 'planned_cases': len(CASES),
                  'task_success_rate': sum(r['task_success'] for r in rows) / len(rows),
                  'recovery_success_rate': None, 'cases': rows,
                  'limitations': ['Three public development tasks; not a general capability benchmark.',
                                  'No fault injection in this live suite; recovery rate unmeasured.',
                                  'Client cost reservation is an estimate, not a provider billing guarantee.']}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'report': str(args.output), 'task_success_rate': result['task_success_rate']}))
    return 0 if all(r['task_success'] for r in rows) else 1


if __name__ == '__main__':
    sys.exit(main())
