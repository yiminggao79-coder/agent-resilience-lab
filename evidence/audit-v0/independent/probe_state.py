"""Read-only V0 audit probes; production and existing tests are untouched."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from agent_lab.tools import Tools


def call(name, args):
    return {'message': {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'independent-audit-call', 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(args)}}]},
        'usage': {'input_tokens': 2, 'output_tokens': 2}}


def finish():
    return {'message': {'role': 'assistant', 'content': 'Finished audit fixture'},
            'usage': {'input_tokens': 2, 'output_tokens': 2}}


class Fixture:
    identity = 'independent-audit-v0-offline'
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.calls = 0
    def complete(self, messages, tools, timeout, max_tokens):
        self.calls += 1
        return next(self.decisions)


def snapshot(state):
    feedback = json.loads(state['messages'][-1]['content']) if state['messages'][-1]['role'] == 'tool' else None
    if feedback and isinstance(feedback.get('content'), str) and len(feedback['content']) > 1024:
        content = feedback.pop('content').encode()
        feedback['content_utf8_bytes'] = len(content)
        feedback['content_sha256'] = hashlib.sha256(content).hexdigest()
    return {'phase': state['phase'], 'reason': state['reason'],
            'pending': copy.deepcopy(state['pending']), 'metrics': state['metrics'],
            'last_message_role': state['messages'][-1]['role'],
            'last_tool_feedback': feedback}


def receipt_failure(operation, fault_type):
    with tempfile.TemporaryDirectory(prefix='arl-audit-') as base:
        root = Path(base) / 'work'; root.mkdir()
        with Store(Path(base) / 'private' / 'state.sqlite') as store:
            fixture = Fixture([call(operation, {'path': 'effect.txt', 'content': 'once\n'}), finish()])
            injected = []
            def hook(event, state):
                if event == 'after_tool_effect' and fault_type == 'persistent_sqlite_readonly' and not injected:
                    injected.append(True)
                    store.db.execute('PRAGMA query_only=ON')
                elif event == 'after_tool_effect' and fault_type == 'sqlite_deny_once' and not injected:
                    injected.append(True)
                    def authorize(action, arg1, arg2, database, trigger):
                        if action == sqlite3.SQLITE_INSERT and arg1 == 'tasks' and len(injected) == 1:
                            injected.append('denied')
                            return sqlite3.SQLITE_DENY
                        return sqlite3.SQLITE_OK
                    store.db.set_authorizer(authorize)
            engine = Engine(store, Tools(root), fixture, hook)
            tid = engine.create('Write or append one record')
            original = store.save
            faults = []
            def save(state, kind, meta=None):
                if kind == 'tool_finished' and not faults:
                    faults.append(kind)
                    if fault_type == 'after_commit_once':
                        original(state, kind, meta)
                    if fault_type not in ('persistent_sqlite_readonly', 'sqlite_deny_once'):
                        raise sqlite3.OperationalError('injected_receipt_storage_failure')
                return original(state, kind, meta)
            escaped = None
            with patch.object(store, 'save', save):
                try:
                    returned = engine.run(tid)
                except Exception as error:
                    escaped = {'type': type(error).__name__, 'text': str(error)}
                    returned = None
            persisted = store.load(tid)
            before = (root / 'effect.txt').read_text()
            events = store.events(tid)
            store.db.execute('PRAGMA query_only=OFF')
            resumed = engine.run(tid)
            return {'operation': operation, 'fault': fault_type, 'escaped': escaped,
                    'returned': snapshot(returned) if returned else None,
                    'durable_before_resume': snapshot(persisted),
                    'trace_kinds': [e['kind'] for e in events],
                    'file_before_resume': before, 'file_after_resume': (root / 'effect.txt').read_text(),
                    'resumed': snapshot(resumed), 'fixture_calls': fixture.calls}


def context_growth():
    with tempfile.TemporaryDirectory(prefix='arl-audit-') as base:
        root = Path(base) / 'work'; root.mkdir()
        (root / 'large.txt').write_text('x' * 65536)
        with Store(Path(base) / 'private' / 'state.sqlite') as store:
            model = Fixture([call('read_file', {'path': 'large.txt'})] * 4 + [finish()])
            engine = Engine(store, Tools(root), model)
            tid = engine.create('Inspect the file four times and then finish')
            state = engine.run(tid)
            retry = engine.run(tid)
            return {'state': snapshot(state), 'message_bytes': len(json.dumps(state['messages']).encode()),
                    'fixture_calls': model.calls, 'resume_reason': retry['reason']}


def state_validation():
    results = []
    with tempfile.TemporaryDirectory(prefix='arl-audit-') as base:
        root = Path(base) / 'work'; root.mkdir()
        with Store(Path(base) / 'private' / 'state.sqlite') as store:
            for mutation in ('unknown_phase', 'unsupported_schema_abort', 'changed_tools_schema'):
                model = Fixture([finish()])
                engine = Engine(store, Tools(root), model)
                tid = engine.create('No operation required')
                state = store.load(tid)
                if mutation == 'unknown_phase':
                    state['phase'] = 'unrecognized_state'
                    store.save(state, 'audit_injected')
                    out = engine.run(tid)
                elif mutation == 'unsupported_schema_abort':
                    state['schema_version'] = 999
                    store.save(state, 'audit_injected')
                    out = engine.abort(tid)
                else:
                    with patch.object(engine.tools, 'schemas', lambda: []):
                        out = engine.run(tid)
                results.append({'case': mutation, 'result': snapshot(out), 'fixture_calls': model.calls,
                                'durable_schema': store.load(tid)['schema_version']})
            model = Fixture([finish()])
            engine = Engine(store, Tools(root), model)
            tid = engine.create('Finish')
            completed = engine.run(tid)
            command = [sys.executable, '-m', 'agent_lab', '--db', str(store.path), 'answer', tid]
            before_answer = subprocess.run(command, capture_output=True, text=True, timeout=5)
            aborted = engine.abort(tid)
            after_answer = subprocess.run(command, capture_output=True, text=True, timeout=5)
            results.append({'case': 'abort_completed', 'before': completed['phase'],
                            'after': aborted['phase'], 'reason': aborted['reason'],
                            'answer_before': {'exit': before_answer.returncode, 'stdout': before_answer.stdout, 'stderr': before_answer.stderr},
                            'answer_after': {'exit': after_answer.returncode, 'stdout': after_answer.stdout, 'stderr': after_answer.stderr}})
    return results


def freeze_check():
    repo = Path(__file__).resolve().parents[3]
    freeze = json.loads((repo / 'evidence/implementation-freeze.json').read_text())
    return {'frozen_commit': freeze['frozen_commit'], 'matches': {
        name: hashlib.sha256((repo / name).read_bytes()).hexdigest() == digest
        for name, digest in freeze['files'].items()}}


if __name__ == '__main__':
    output = {'fixture_warning': 'All model decisions synthetic; real tools, SQLite and files.',
              'freeze': freeze_check(),
              'receipt_failures': [receipt_failure(op, fault) for op in ('write_file', 'append_file')
                                   for fault in ('before_commit_once', 'after_commit_once', 'sqlite_deny_once', 'persistent_sqlite_readonly')],
              'context_growth': context_growth(), 'state_validation': state_validation()}
    print(json.dumps(output, indent=2))
