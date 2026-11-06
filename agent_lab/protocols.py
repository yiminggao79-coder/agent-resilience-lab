"""Provider wire formats: text may stream; executable turns require completion."""
import copy
import json


class ProtocolError(ValueError):
    """A provider reply cannot safely be interpreted as a complete turn."""


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _object_arguments(arguments):
    if not isinstance(arguments, str):
        raise ProtocolError('invalid_tool_arguments')
    try:
        value = json.loads(arguments, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError):
        raise ProtocolError('invalid_tool_arguments')
    if not isinstance(value, dict):
        raise ProtocolError('invalid_tool_arguments')
    return value


def _call(call_id, name, arguments):
    if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
        raise ProtocolError('invalid_tool_call')
    # Completion/framing authorizes handing opaque arguments to Engine, not
    # executing them. Complete but invalid JSON must receive tool feedback.
    if not isinstance(arguments, str):
        raise ProtocolError('invalid_tool_arguments')
    return {'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}


def _anthropic_content(blocks):
    """Validate native blocks and derive the only text/tools Engine may act on."""
    if not isinstance(blocks, list):
        raise ProtocolError('invalid_native_content')
    texts, calls = [], []
    for block in blocks:
        if not isinstance(block, dict):
            raise ProtocolError('invalid_native_content')
        kind = block.get('type')
        if kind == 'text':
            if not isinstance(block.get('text'), str):
                raise ProtocolError('invalid_content')
            texts.append(block['text'])
        elif kind == 'tool_use':
            if not isinstance(block.get('input'), dict):
                raise ProtocolError('invalid_tool_arguments')
            calls.append(_call(block.get('id'), block.get('name'), _dump(block['input'])))
        elif kind == 'thinking':
            if not isinstance(block.get('thinking'), str) or not isinstance(block.get('signature'), str):
                raise ProtocolError('invalid_thinking_content')
        elif kind == 'redacted_thinking':
            if not isinstance(block.get('data'), str):
                raise ProtocolError('invalid_thinking_content')
        else:
            raise ProtocolError('unsupported_content')
    if len({call['id'] for call in calls}) != len(calls):
        raise ProtocolError('duplicate_tool_id')
    # Keep unknown metadata on supported blocks, but never non-JSON values.
    try:
        _dump(blocks)
    except (TypeError, ValueError):
        raise ProtocolError('invalid_native_content')
    return ''.join(texts) or None, calls


def _tool_projection(calls):
    # JSON types must agree too: Python considers True == 1, unlike tool inputs.
    return [(call['id'], call['function']['name'],
             json.dumps(_object_arguments(call['function']['arguments']), sort_keys=True, allow_nan=False))
            for call in calls]


def _responses_content(items):
    """Keep replayable output private; derive only visible text and tool calls."""
    if not isinstance(items, list):
        raise ProtocolError('invalid_native_content')
    texts, calls = [], []
    for item in items:
        if not isinstance(item, dict):
            raise ProtocolError('invalid_native_content')
        if item.get('status') not in (None, 'completed'):
            raise ProtocolError('incomplete_item')
        for field in ('id', 'encrypted_content'):
            if item.get(field) is not None and not isinstance(item[field], str):
                raise ProtocolError('invalid_native_content')
        kind = item.get('type')
        if kind == 'function_call':
            calls.append(_call(item.get('call_id'), item.get('name'), item.get('arguments')))
        elif kind == 'message':
            if item.get('role', 'assistant') != 'assistant':
                raise ProtocolError('invalid_message_role')
            if item.get('phase') is not None and not isinstance(item['phase'], str):
                raise ProtocolError('invalid_native_content')
            blocks = item.get('content')
            if not isinstance(blocks, list):
                raise ProtocolError('invalid_content')
            for block in blocks:
                if not isinstance(block, dict) or block.get('type') != 'output_text' or not isinstance(block.get('text'), str):
                    raise ProtocolError('unsupported_content')
                texts.append(block['text'])
        elif kind == 'reasoning':
            for field, block_type in (('summary', 'summary_text'), ('content', 'reasoning_text')):
                blocks = item.get(field, [])
                if field == 'content' and blocks is None:
                    continue
                if not isinstance(blocks, list) or any(not isinstance(block, dict)
                        or block.get('type') != block_type or not isinstance(block.get('text'), str) for block in blocks):
                    raise ProtocolError('invalid_reasoning_content')
        else:
            raise ProtocolError('unsupported_output')
    if len({call['id'] for call in calls}) != len(calls):
        raise ProtocolError('duplicate_tool_id')
    try:
        _dump(items)
    except (TypeError, ValueError):
        raise ProtocolError('invalid_native_content')
    return ''.join(texts) or None, calls


def _responses_call_projection(calls):
    # Arguments remain opaque, including invalid JSON that needs tool feedback.
    if not isinstance(calls, list) or any(not isinstance(call, dict)
            or call.get('type') != 'function' for call in calls):
        raise ProtocolError('invalid_tool_call')
    try:
        return [_call(call['id'], call['function']['name'], call['function']['arguments']) for call in calls]
    except (KeyError, TypeError):
        raise ProtocolError('invalid_tool_call') from None


def build_request(protocol, model, messages, tools, max_tokens, stream=False,
                  token_parameter='max_completion_tokens', parallel_parameter=True,
                  thinking=None, reasoning_effort=None):
    if protocol not in ('chat', 'responses', 'anthropic'):
        raise ProtocolError('invalid_protocol')
    if token_parameter not in ('max_completion_tokens', 'max_tokens'):
        raise ProtocolError('invalid_token_parameter')
    if type(stream) is not bool or type(parallel_parameter) is not bool:
        raise ProtocolError('invalid_capability')
    if type(max_tokens) is not int or max_tokens <= 0:
        raise ProtocolError('invalid_max_tokens')
    if protocol == 'responses' and (thinking is not None or reasoning_effort is not None):
        raise ProtocolError('unsupported_thinking_options')
    modes = ('enabled', 'disabled', 'adaptive') if protocol == 'anthropic' else ('enabled', 'disabled')
    if thinking is not None and thinking not in modes:
        raise ProtocolError('invalid_thinking')
    if reasoning_effort is not None and reasoning_effort not in ('minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
        raise ProtocolError('invalid_reasoning_effort')
    if protocol == 'chat':
        body = {'model': model, 'messages': messages, 'tools': tools, 'tool_choice': 'auto',
                token_parameter: max_tokens, 'stream': stream, 'store': False}
        if parallel_parameter:
            body['parallel_tool_calls'] = False
        if stream:
            body['stream_options'] = {'include_usage': True}
        if thinking is not None:
            body['thinking'] = {'type': thinking}
        if reasoning_effort is not None:
            body['reasoning_effort'] = reasoning_effort
        return '/chat/completions', body
    if protocol == 'responses':
        items = []
        for message in messages:
            role, content = message['role'], message.get('content')
            if 'native_content' in message:
                if role != 'assistant':
                    raise ProtocolError('invalid_native_content_role')
                native_text, native_calls = _responses_content(message['native_content'])
                if native_text != (content or None) or native_calls != _responses_call_projection(message.get('tool_calls', [])):
                    raise ProtocolError('native_content_mismatch')
                items.extend(copy.deepcopy(message['native_content']))
                continue
            if role == 'tool':
                items.append({'type': 'function_call_output', 'call_id': message['tool_call_id'], 'output': content})
            else:
                if content is not None:
                    items.append({'role': role, 'content': content})
                for call in message.get('tool_calls', []):
                    items.append({'type': 'function_call', 'call_id': call['id'],
                                  'name': call['function']['name'], 'arguments': call['function']['arguments']})
        body = {'model': model, 'input': items,
                'tools': [dict({'type': 'function'}, **tool['function']) for tool in tools],
                'tool_choice': 'auto', 'max_output_tokens': max_tokens, 'stream': stream, 'store': False,
                'include': ['reasoning.encrypted_content']}
        if parallel_parameter:
            body['parallel_tool_calls'] = False
        return '/responses', body
    history, system = [], []
    for message in messages:
        role, content = message['role'], message.get('content')
        if 'native_content' in message and role != 'assistant':
            raise ProtocolError('invalid_native_content_role')
        if role in ('system', 'developer'):
            if content:
                system.append(content)
            continue
        if role == 'tool':
            block = {'type': 'tool_result', 'tool_use_id': message['tool_call_id'], 'content': content}
            try:
                result = json.loads(content)
                if isinstance(result, dict) and result.get('ok') is False:
                    block['is_error'] = True
            except (TypeError, ValueError):
                pass
            blocks, role = [block], 'user'
        elif 'native_content' in message:
            native_text, native_calls = _anthropic_content(message['native_content'])
            if native_text != (content or None) or _tool_projection(native_calls) != _tool_projection(message.get('tool_calls', [])):
                raise ProtocolError('native_content_mismatch')
            blocks = copy.deepcopy(message['native_content'])
        else:
            blocks = [{'type': 'text', 'text': content}] if content else []
            for call in message.get('tool_calls', []):
                blocks.append({'type': 'tool_use', 'id': call['id'], 'name': call['function']['name'],
                               'input': _object_arguments(call['function']['arguments'])})
        # Adjacent tool results belong to one user turn in the Messages API.
        if history and history[-1]['role'] == role:
            history[-1]['content'].extend(blocks)
        else:
            history.append({'role': role, 'content': blocks})
    body = {'model': model, 'messages': history, 'max_tokens': max_tokens, 'stream': stream,
            'tools': [{'name': t['function']['name'], 'description': t['function'].get('description', ''),
                       'input_schema': t['function']['parameters']} for t in tools]}
    if tools:
        body['tool_choice'] = {'type': 'auto'}
        if parallel_parameter:
            body['tool_choice']['disable_parallel_tool_use'] = True
    if system:
        body['system'] = '\n\n'.join(system)
    if thinking is not None:
        body['thinking'] = {'type': thinking}
    if reasoning_effort is not None:
        body['output_config'] = {'effort': reasoning_effort}
    return '/messages', body


def _usage(value, input_name='input_tokens', output_name='output_tokens'):
    if not isinstance(value, dict):
        return None
    result = {'input_tokens': value.get(input_name), 'output_tokens': value.get(output_name)}
    return result if all(type(n) is int and n >= 0 for n in result.values()) else None


def _anthropic_usage(value):
    usage = _usage(value)
    if usage is None:
        return None
    cached = [value.get(name, 0) for name in ('cache_read_input_tokens', 'cache_creation_input_tokens')]
    if any(type(n) is not int or n < 0 for n in cached):
        return None
    # Anthropic's input_tokens excludes cache categories. Canonical input is the
    # total; charging all of it at the cache-miss rate is a conservative bound.
    usage['input_tokens'] += sum(cached)
    return usage


def _normalize(protocol, body):
    if not isinstance(body, dict) or body.get('error') is not None:
        raise ProtocolError('invalid_response')
    message = {'role': 'assistant', 'content': None}
    calls, texts = [], []
    if protocol == 'chat':
        choice = body['choices'][0]
        finish = choice.get('finish_reason')
        if finish == 'length':
            raise ProtocolError('output_token_limit')
        if finish not in (None, 'stop', 'tool_calls'):
            raise ProtocolError('incomplete_response')
        source = choice['message']
        if not isinstance(source, dict) or source.get('role') != 'assistant' or source.get('refusal'):
            raise ProtocolError('invalid_message')
        content = source.get('content')
        if content is not None and not isinstance(content, str):
            raise ProtocolError('invalid_content')
        message['content'] = content
        if source.get('reasoning_content') is not None:
            if not isinstance(source['reasoning_content'], str):
                raise ProtocolError('invalid_reasoning')
            message['reasoning_content'] = source['reasoning_content']
        for call in source.get('tool_calls', []):
            if call.get('type', 'function') != 'function':
                raise ProtocolError('invalid_tool_call')
            calls.append(_call(call['id'], call['function']['name'], call['function']['arguments']))
        if finish == 'tool_calls' and not calls:
            raise ProtocolError('missing_tool_calls')
        usage = _usage(body.get('usage'), 'prompt_tokens', 'completion_tokens')
    elif protocol == 'anthropic':
        finish = body.get('stop_reason')
        if finish == 'max_tokens':
            raise ProtocolError('output_token_limit')
        if finish not in ('end_turn', 'tool_use', 'stop_sequence'):
            raise ProtocolError('incomplete_response')
        message['content'], calls = _anthropic_content(body['content'])
        message['native_content'] = copy.deepcopy(body['content'])
        if finish == 'tool_use' and not calls:
            raise ProtocolError('missing_tool_calls')
        usage = _anthropic_usage(body.get('usage'))
    elif protocol == 'responses':
        finish = body.get('status')
        if (finish == 'incomplete' and isinstance(body.get('incomplete_details'), dict)
                and body['incomplete_details'].get('reason') == 'max_output_tokens'):
            raise ProtocolError('output_token_limit')
        if finish != 'completed':
            raise ProtocolError('incomplete_response')
        message['content'], calls = _responses_content(body['output'])
        message['native_content'] = copy.deepcopy(body['output'])
        usage = _usage(body.get('usage'))
    else:
        raise ProtocolError('invalid_protocol')
    if len({c['id'] for c in calls}) != len(calls):
        raise ProtocolError('duplicate_tool_id')
    if calls:
        message['tool_calls'] = calls
    request_id = body.get('id')
    return {'message': message, 'usage': usage,
            'request_id': request_id if isinstance(request_id, str) else None, 'finish_reason': finish}


def normalize_response(protocol, body):
    try:
        return _normalize(protocol, body)
    except ProtocolError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise ProtocolError('invalid_response')


def _events(lines):
    data = []
    for line in lines:
        if not isinstance(line, str):
            raise ProtocolError('invalid_sse')
        line = line.rstrip('\r\n')
        if not line:
            if data:
                joined = '\n'.join(data)
                data = []
                if joined == '[DONE]':
                    yield '[DONE]'
                else:
                    try:
                        event = json.loads(joined)
                    except ValueError:
                        raise ProtocolError('invalid_sse_json')
                    if not isinstance(event, dict):
                        raise ProtocolError('invalid_sse_event')
                    yield event
        elif line.startswith('data:'):
            data.append(line[5:].removeprefix(' '))
    if data:
        raise ProtocolError('incomplete_sse_event')


def _text_delta(callback, value):
    if not isinstance(value, str):
        raise ProtocolError('invalid_text_delta')
    if callback is not None and value:
        try:
            callback(value)
        except ProtocolError:
            raise  # The trusted transport uses this for output-limit failure.
        except Exception:
            pass  # Presentation failures cannot authorize or corrupt a turn.


def _chat_stream(events, on_text=None):
    message = {'role': 'assistant', 'content': ''}
    calls, finish, done, request_id, usage = {}, None, False, None, None
    for event in events:
        if done:
            raise ProtocolError('data_after_done')
        if event == '[DONE]':
            done = True
            continue
        if 'error' in event:
            raise ProtocolError('provider_error')
        if 'id' in event:
            request_id = event['id']
        if 'usage' in event:
            usage = event['usage']
        for choice in event.get('choices', []):
            if choice.get('index', 0) != 0:
                raise ProtocolError('multiple_choices')
            delta = choice.get('delta', {})
            if finish is not None and delta:
                raise ProtocolError('delta_after_finish')
            for key in ('content', 'reasoning_content'):
                value = delta.get(key)
                if value is not None:
                    if not isinstance(value, str):
                        raise ProtocolError('invalid_delta')
                    message[key] = message.get(key, '') + value
                    if key == 'content':
                        _text_delta(on_text, value)
            if delta.get('refusal'):
                raise ProtocolError('refusal')
            for part in delta.get('tool_calls', []):
                index = part['index']
                if type(index) is not int or index < 0:
                    raise ProtocolError('invalid_tool_index')
                call = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                if part.get('type', 'function') != 'function':
                    raise ProtocolError('invalid_tool_type')
                if 'id' in part:
                    call['id'] += part['id']
                for key in ('name', 'arguments'):
                    if key in part.get('function', {}):
                        call['function'][key] += part['function'][key]
            if choice.get('finish_reason') is not None:
                if finish is not None:
                    raise ProtocolError('duplicate_finish')
                finish = choice['finish_reason']
    if finish == 'length':
        raise ProtocolError('output_token_limit')
    if not done or finish not in ('stop', 'tool_calls'):
        raise ProtocolError('incomplete_stream')
    if calls:
        if sorted(calls) != list(range(len(calls))):
            raise ProtocolError('incomplete_tool_calls')
        message['tool_calls'] = [calls[i] for i in sorted(calls)]
    return normalize_response('chat', {'id': request_id, 'choices': [{'message': message, 'finish_reason': finish}], 'usage': usage})


def _anthropic_stream(events, on_text=None):
    body, blocks, closed, stopped = None, {}, set(), False
    invalid_usage = False
    for event in events:
        if event == '[DONE]' or stopped:
            raise ProtocolError('unexpected_event')
        kind = event.get('type')
        if kind == 'message_start':
            if body is not None:
                raise ProtocolError('duplicate_start')
            body = dict(event['message'])
            invalid_usage = _usage(body.get('usage')) is None
            body['usage'] = dict(body.get('usage', {})) if isinstance(body.get('usage'), dict) else {}
        elif kind == 'ping':
            continue
        elif body is None:
            raise ProtocolError('missing_start')
        elif kind == 'content_block_start':
            index = event['index']
            if type(index) is not int or index < 0 or index in blocks:
                raise ProtocolError('invalid_block')
            blocks[index] = {'block': dict(event['content_block']), 'json': ''}
            if blocks[index]['block'].get('type') == 'text':
                _text_delta(on_text, blocks[index]['block'].get('text'))
        elif kind == 'content_block_delta':
            index, delta = event['index'], event['delta']
            if index not in blocks or index in closed:
                raise ProtocolError('delta_outside_block')
            state = blocks[index]
            if delta['type'] == 'input_json_delta' and state['block']['type'] == 'tool_use':
                state['json'] += delta['partial_json']
            elif delta['type'] == 'text_delta' and state['block']['type'] == 'text':
                state['block']['text'] += delta['text']
                _text_delta(on_text, delta['text'])
            elif delta['type'] == 'thinking_delta' and state['block']['type'] == 'thinking':
                state['block']['thinking'] += delta['thinking']
            elif delta['type'] == 'signature_delta' and state['block']['type'] == 'thinking':
                state['block']['signature'] = state['block'].get('signature', '') + delta['signature']
            else:
                raise ProtocolError('unsupported_delta')
        elif kind == 'content_block_stop':
            index = event['index']
            if index not in blocks or index in closed:
                raise ProtocolError('invalid_block_stop')
            closed.add(index)
        elif kind == 'message_delta':
            body['stop_reason'] = event.get('delta', {}).get('stop_reason')
            if 'usage' in event:
                if isinstance(event['usage'], dict):
                    body['usage'].update(event['usage'])
                else:
                    invalid_usage = True
        elif kind == 'message_stop':
            stopped = True
        else:
            raise ProtocolError('unexpected_event')
    if body is not None and body.get('stop_reason') == 'max_tokens':
        raise ProtocolError('output_token_limit')
    if not stopped or body is None or set(blocks) != closed or sorted(blocks) != list(range(len(blocks))):
        raise ProtocolError('incomplete_stream')
    for state in blocks.values():
        if state['json']:
            state['block']['input'] = _object_arguments(state['json'])
    body['content'] = [blocks[i]['block'] for i in sorted(blocks)]
    if invalid_usage:
        body['usage'] = None
    return normalize_response('anthropic', body)


def _responses_stream(events, on_text=None):
    completed, opened, closed = None, set(), {}
    for event in events:
        if event == '[DONE]':
            raise ProtocolError('unexpected_done')
        if completed is not None:
            raise ProtocolError('event_after_completed')
        kind = event.get('type', '')
        if kind in ('error', 'response.failed', 'response.incomplete'):
            response = event.get('response')
            if (kind == 'response.incomplete' and isinstance(response, dict)
                    and response.get('status') == 'incomplete'
                    and isinstance(response.get('incomplete_details'), dict)
                    and response['incomplete_details'].get('reason') == 'max_output_tokens'):
                raise ProtocolError('output_token_limit')
            raise ProtocolError('incomplete_response')
        if kind == 'response.output_item.added':
            index = event['output_index']
            if type(index) is not int or index < 0 or index in opened:
                raise ProtocolError('invalid_item')
            opened.add(index)
        elif kind == 'response.output_item.done':
            index = event['output_index']
            if index not in opened or index in closed:
                raise ProtocolError('invalid_item_done')
            _responses_content([event['item']])
            closed[index] = copy.deepcopy(event['item'])
        elif kind == 'response.output_text.delta':
            _text_delta(on_text, event['delta'])
        elif kind == 'response.completed':
            if opened != set(closed):
                raise ProtocolError('incomplete_items')
            completed = normalize_response('responses', event['response'])
            if opened:
                if sorted(closed) != list(range(len(closed))):
                    raise ProtocolError('incomplete_items')
                items = [closed[index] for index in sorted(closed)]
                final_items = completed['message']['native_content']
                if (_responses_content(items) != _responses_content(final_items)
                        or [(item['type'], item.get('id')) for item in items]
                        != [(item['type'], item.get('id')) for item in final_items]):
                    raise ProtocolError('completed_items_mismatch')
                # Added reasoning may carry incomplete ciphertext. The API's
                # completed item is the replay source; completed gates execution.
                completed['message']['native_content'] = items
    if completed is None:
        raise ProtocolError('incomplete_stream')
    return completed


def consume_sse(protocol, lines, on_text=None):
    """Present text incrementally; only completed turns return executable calls."""
    try:
        parser = {'chat': _chat_stream, 'anthropic': _anthropic_stream, 'responses': _responses_stream}[protocol]
        return parser(_events(lines), on_text)
    except ProtocolError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise ProtocolError('invalid_stream')
