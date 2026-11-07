"""Offline protocol doubles. Never used as autonomous Agent evidence."""
import json
import uuid


def tool(name, args, call_id=None):
    call_id = call_id or uuid.uuid4().hex
    return {'message': {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]},
        'usage': {'input_tokens': 100, 'output_tokens': 40}}


def final(text='finished'):
    return {'message': {'role': 'assistant', 'content': text}, 'usage': {'input_tokens': 100, 'output_tokens': 20}}


class ScriptedModel:
    identity = 'offline_scripted_protocol_double'

    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen = []

    def complete(self, messages, tools, timeout, max_tokens):
        self.seen.append(json.loads(json.dumps(messages)))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response
