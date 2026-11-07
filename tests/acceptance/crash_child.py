"""Acceptance-only process stopped after a real effect and before its receipt."""
import json
from pathlib import Path
import signal
import sys

from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.acceptance.protocol_fixture import ProtocolFixture, finish, function_call


def main():
    root, database, mode, operation = sys.argv[1:5]
    decisions = [finish('Recovered offline protocol fixture.')]
    if mode == 'crash':
        decisions.insert(0, function_call(operation, {'path': 'receipt.txt', 'content': 'accepted-once\n'}))
    model = ProtocolFixture(decisions)

    def hook(event, state):
        if mode == 'crash' and event == 'after_tool_effect':
            print(json.dumps({'task_id': state['id'], 'window': 'effect_before_receipt'}), flush=True)
            while True:
                signal.pause()

    with Store(Path(database)) as store:
        engine = Engine(store, Tools(Path(root)), model, hook=hook)
        task_id = engine.create('Write the acceptance receipt once.', Limits(max_seconds=60)) if mode == 'crash' else sys.argv[5]
        state = engine.run(task_id)
        print(json.dumps({'phase': state['phase'], 'reason': state['reason'],
                          'model_calls_this_process': model.calls}), flush=True)


if __name__ == '__main__':
    main()
