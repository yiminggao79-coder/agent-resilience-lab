"""Subprocess driver for fault injection. Scripted protocol, never a real LLM."""
import json
from pathlib import Path
import signal
import sys
from agent_lab.engine import Engine, Limits, public_status
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel, tool, final


def main():
    root, database, mode, operation, stage = sys.argv[1:]
    with Store(database) as store:
        messages = [tool(operation, {'path': 'result.txt', 'content': 'one\n'}), final('offline fixture finished')]
        if mode == 'resume':
            model = ScriptedModel([final('offline fixture resumed')])
            task_id = next(store.db.execute('SELECT id FROM tasks ORDER BY rowid DESC'))[0]
        else:
            model = ScriptedModel(messages)

        def pause(event, state):
            if mode == 'crash' and event == stage:
                print(json.dumps({'ready': True, 'task_id': state['id'], 'stage': event}), flush=True)
                while True:
                    signal.pause()

        engine = Engine(store, Tools(root), model, pause)
        if mode != 'resume':
            task_id = engine.create('Offline crash fixture: create result.txt', Limits(max_seconds=120))
        state = engine.run(task_id)
        print(json.dumps(public_status(state)), flush=True)


if __name__ == '__main__':
    main()
