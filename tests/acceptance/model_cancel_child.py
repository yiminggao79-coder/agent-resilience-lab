"""Owned synthetic HTTP Runner for process lifecycle acceptance (no paid API)."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from agent_lab import model as transport
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.model import OpenAIModel
from agent_lab.store import Store


def main():
    root, db, marker, url, streaming = sys.argv[1:]
    os.environ['LAB_MODEL_CANCEL_KEY'] = 'synthetic-process-only'
    model = OpenAIModel('synthetic', url, 'LAB_MODEL_CANCEL_KEY', True, stream=streaming == 'true')
    with Store(db) as store:
        engine = Engine(store, CodingTools(root), model)
        tid = engine.create('Inspect and modify the project, then run tests.', Limits(model_timeout=15, max_seconds=30))
        turn_id = store.load(tid)['turn_id']
        original = subprocess.Popen
        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            Path(marker).write_text(json.dumps({'task_id': tid, 'turn_id': turn_id, 'runner_pid': os.getpid(), 'worker_pid': process.pid}))
            return process
        with patch.object(transport.subprocess, 'Popen', side_effect=launch):
            state = engine.run(tid)
        print(json.dumps({'phase': state['phase'], 'reason': state['reason'], 'usage_unknown': state.get('usage_unknown'),
                          'local_tools_accepted': state.get('last_model_cancellation', {}).get('local_tools_accepted')}), flush=True)


if __name__ == '__main__':
    main()
