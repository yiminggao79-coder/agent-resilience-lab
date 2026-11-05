"""A synchronous usable CLI conversation, with explicit one-operation approval.

The prompt is synchronous; a second CLI process can use the durable steering
inbox while Engine.run owns the workspace lock. Ctrl-C uses the conservative
interruption contract. New user text never resolves an unknown effect.
"""
import json

from .engine import public_status
from .store import Busy
from .model import ModelError


def chat_loop(engine, *, task_id=None, limits=None, read=input, write=print, stream_text=False,
              track_file_context=False):
    if type(track_file_context) is not bool or (track_file_context and task_id is not None):
        raise ValueError('file_context_requires_new_task')
    write('Agent Resilience Lab — /exit /status /turns /plan /policy /compact /resume /abort /help')
    if task_id is not None:
        write(json.dumps(public_status(engine.store.load(task_id)), ensure_ascii=True))

    stop_requested = False

    def run_turn():
        nonlocal stop_requested
        state = engine.run(task_id)
        while state['phase'] == 'awaiting_approval':
            request = engine.approval_request(task_id)
            write(json.dumps({'approval': request}, ensure_ascii=True))
            try:
                decision = read('approve once [yes/NO/exit]> ').strip().lower()
            except (EOFError, KeyboardInterrupt):
                stop_requested = True
                return state  # Keep the durable waiting action available.
            if decision in ('exit', '/exit'):
                stop_requested = True
                return state
            engine.decide_approval(task_id, request['request_id'], request['digest'], decision in ('yes', 'y'))
            state = engine.run(task_id)
        write(json.dumps(public_status(state), ensure_ascii=True))
        if state['phase'] == 'completed' and not (stream_text and getattr(engine.model, 'last_text_streamed', False)):
            write(state['messages'][-1].get('content') or '')
        return state

    while not stop_requested:
        try:
            text = read('you> ').strip()
        except (EOFError, KeyboardInterrupt):
            return task_id
        if not text:
            continue
        if text == '/exit':
            return task_id
        if text == '/help':
            write('Enter a task or follow-up. /plan and /policy explicitly display private plan/policy details. /compact requests a paid model summary within existing quotas, preserving original archive. /resume continues the same turn; /abort never resolves unknown effects. Only yes/y approves the displayed exact command. /exit leaves checkpoints available.')
            continue
        try:
            if text in ('/status', '/turns', '/plan', '/policy', '/compact', '/abort', '/resume'):
                if task_id is None:
                    write('No task yet.')
                    continue
                if text == '/resume':
                    run_turn()
                elif text == '/abort':
                    write(json.dumps(public_status(engine.abort(task_id)), ensure_ascii=True))
                elif text == '/plan':
                    write(json.dumps({'plan':engine.store.load(task_id).get('current_plan')},ensure_ascii=True))
                elif text == '/compact':
                    write(json.dumps(public_status(engine.compact(task_id)),ensure_ascii=True))
                elif text == '/policy':
                    state=engine.store.load(task_id)
                    write(json.dumps({'read_only_policy':state.get('read_only_policy',{'version':1,'read_only_paths':[]}),
                                      'identity':state.get('read_only_policy_identity')},ensure_ascii=True))
                else:
                    value = engine.store.turns(task_id) if text == '/turns' else public_status(engine.store.load(task_id))
                    write(json.dumps(value, ensure_ascii=True))
                continue
            if text.startswith('/'):
                write('Unknown command; use /help.')
                continue
            if task_id is None:
                task_id = engine.create(text, limits,
                                        **({'track_file_context': True} if track_file_context else {}))
            else:
                engine.continue_turn(task_id, text, limits)
            run_turn()
        except (ValueError, KeyError, Busy):
            # Never stringify exceptions containing model/user data.
            write('Cannot continue this state/configuration; inspect /status or use /resume for a pending turn.')
        except ModelError as error:
            write(json.dumps({'error':error.code}))
    return task_id
