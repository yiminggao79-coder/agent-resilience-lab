"""Explicit deterministic protocol fixture, authored independently of dev tests."""
import json
import uuid


def function_call(name, arguments, call_id=None):
    call_id = call_id or uuid.uuid4().hex
    return {'message': {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': call_id, 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(arguments)}}]},
        'usage': {'input_tokens': 17, 'output_tokens': 5}}


def finish(text='Offline fixture finished.'):
    return {'message': {'role': 'assistant', 'content': text},
            'usage': {'input_tokens': 17, 'output_tokens': 5}}


def feedback(messages):
    assert messages[-1]['role'] == 'tool', 'Expected actual tool feedback'
    return json.loads(messages[-1]['content'])


class ProtocolFixture:
    identity = 'independent-acceptance-offline-protocol-v1'

    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.calls = 0

    def complete(self, messages, tools, timeout, max_tokens):
        self.calls += 1
        decision = next(self.decisions)
        return decision(messages) if callable(decision) else decision
