"""Synthetic real stdio MCP server for local boundary tests; no network or keys."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

mode = sys.argv[1]
log = Path(sys.argv[2])
marker = Path(sys.argv[3])
initialized = False
last_id = 0


def send(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)


def reply(request, result):
    send({'jsonrpc': '2.0', 'id': request['id'], 'result': result})


def tool(name):
    return {'name': name, 'description': 'synthetic local tool',
            'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': False}}


def spawn_child():
    code = ('import pathlib,sys,time,signal\n'
            'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
            'p=pathlib.Path(sys.argv[1])\n'
            'while True:\n p.write_text(str(time.monotonic())); time.sleep(.02)\n')
    child = subprocess.Popen([sys.executable, '-I', '-S', '-c', code, str(marker)])
    deadline = time.monotonic() + 2
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(.005)
    return child.pid


if mode == 'ignore_term':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
for line in sys.stdin:
    request = json.loads(line)
    with log.open('a') as out:
        out.write(json.dumps(request) + '\n')
    if 'method' not in request:  # Reply to a server-initiated ping/unknown request.
        continue
    method = request['method']
    if method == 'initialize':
        assert request['id'] == 1
        assert request['params']['protocolVersion'] == '2025-11-25'
        assert request['params']['capabilities'] == {}
        version = '2099-01-01' if mode == 'bad_version' else ('2025-06-18' if mode == 'old_version' else '2025-11-25')
        reply(request, {'protocolVersion': version, 'capabilities': {} if mode == 'no_tools' else {'tools': {'listChanged': True}},
                        'serverInfo': {'name': 'synthetic', 'version': '1.0'}})
        print('PRIVATE_SYNTHETIC_STDERR', file=sys.stderr, flush=True)
        continue
    if method == 'notifications/initialized':
        initialized = True
        continue
    if method == 'notifications/cancelled':
        continue
    assert initialized
    assert type(request['id']) is int and request['id'] > last_id
    last_id = request['id']
    if method == 'tools/list':
        if mode == 'repeat_cursor':
            reply(request, {'tools': [], 'nextCursor': 'same'})
        elif mode == 'too_many_tools':
            reply(request, {'tools': [tool('t' + str(i)) for i in range(10)]})
        elif mode == 'bad_tool':
            reply(request, {'tools': [{'name': 'echo', 'inputSchema': None}]})
        elif request.get('params', {}).get('cursor'):
            reply(request, {'tools': [tool('mutate')]})
        else:
            reply(request, {'tools': [tool('echo'), tool('inspect')], 'nextCursor': 'page2'})
        continue
    assert method == 'tools/call'
    if mode in ('effect_timeout', 'die'):
        with marker.open('a') as out:
            out.write('effect\n')
    if mode == 'die':
        os._exit(7)
    if mode in ('slow', 'effect_timeout'):
        time.sleep(30)
    if mode == 'eof':
        break
    if mode == 'partial':
        sys.stdout.write('{"jsonrpc":"2.0"')
        sys.stdout.flush()
        break
    if mode == 'malformed':
        print('PRIVATE_PROVIDER_ERROR not json', flush=True)
        continue
    if mode == 'wrongid':
        send({'jsonrpc': '2.0', 'id': request['id'] + 1, 'result': {}})
        continue
    if mode == 'boolean_id':
        send({'jsonrpc': '2.0', 'id': True, 'result': {}})
        continue
    if mode == 'double_envelope':
        send({'jsonrpc': '2.0', 'id': request['id'], 'result': {}, 'error': {'code': -1, 'message': 'PRIVATE'}})
        continue
    if mode == 'rpc_error':
        send({'jsonrpc': '2.0', 'id': request['id'], 'error': {'code': -32603, 'message': 'PRIVATE_PROVIDER_ERROR', 'data': 'SECRET'}})
        continue
    if mode == 'stdout_flood':
        sys.stdout.write('x' * 100000)
        sys.stdout.flush()
        continue
    if mode == 'stderr_flood':
        sys.stderr.write('PRIVATE' * 20000)
        sys.stderr.flush()
        continue
    if mode == 'notification_timeout':
        while True:
            send({'jsonrpc': '2.0', 'method': 'notifications/progress', 'params': {'progressToken': 'test', 'progress': 1}})
            time.sleep(.03)
    if mode == 'duplicate_response':
        value = {'jsonrpc': '2.0', 'id': request['id'], 'result': {'content': [], 'isError': False}}
        sys.stdout.write(json.dumps(value) + '\n' + json.dumps(value) + '\n')
        sys.stdout.flush()
        continue
    if mode == 'duplicate_keys':
        print('{"jsonrpc":"2.0","id":' + str(request['id']) + ',"result":{},"result":{}}', flush=True)
        continue
    if mode == 'notification_flood':
        while True:
            send({'jsonrpc': '2.0', 'method': 'notifications/message', 'params': {'data': 'PRIVATE'}})
    if mode == 'notifications':
        send({'jsonrpc': '2.0', 'method': 'notifications/message', 'params': {'data': 'PRIVATE'}})
        send({'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed'})
    if mode == 'server_request':
        send({'jsonrpc': '2.0', 'id': 'server-ping', 'method': 'ping'})
        received = json.loads(sys.stdin.readline())
        with log.open('a') as out:
            out.write(json.dumps(received) + '\n')
        assert received == {'jsonrpc': '2.0', 'id': 'server-ping', 'result': {}}
        send({'jsonrpc': '2.0', 'id': 'server-sampling', 'method': 'sampling/createMessage', 'params': {}})
        received = json.loads(sys.stdin.readline())
        assert received['error']['code'] == -32601
    if mode in ('spawn', 'parent_dies'):
        pid = spawn_child()
        if mode == 'parent_dies':
            os._exit(7)
    args = request['params']['arguments']
    value = {'env_keys': sorted(os.environ), 'value': os.environ.get('SYNTHETIC_ALLOWED')} if request['params']['name'] == 'inspect' else args
    result = {'content': [{'type': 'text', 'text': 'real local stdio result\nsecond line'}],
              'structuredContent': value, 'isError': bool(args.get('fail'))}
    if mode == 'bad_result':
        result['isError'] = 'false'
    reply(request, result)
if mode == 'ignore_term':
    time.sleep(30)
