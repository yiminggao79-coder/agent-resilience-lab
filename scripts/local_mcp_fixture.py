#!/usr/bin/env python3
"""Owned synthetic local stdio MCP service; no third-party certification claim."""
import json
import os
from pathlib import Path
import sys

TOOL = {'name':'fetch_policy', 'description':'Read the current synthetic quote policy and exact receipt. No arguments.',
        'inputSchema':{'type':'object','properties':{},'additionalProperties':False}}
SERVER_NAME = 'owned_synthetic_receipt'


def main():
    state, log = map(Path, sys.argv[1:])
    with state.open('rb') as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        return 2
    policy = json.loads(raw)
    for line in sys.stdin:
        if len(line) > 65536:
            return 2
        request = json.loads(line)
        method, params = request.get('method'), request.get('params', {})
        with log.open('a') as stream:
            stream.write(json.dumps({'method':method})+'\n')
        if 'id' not in request:
            continue
        if method == 'initialize':
            result = {'protocolVersion':'2025-11-25','capabilities':{'tools':{}},
                      'serverInfo':{'name':SERVER_NAME,'version':'1'}}
        elif method == 'tools/list':
            result = {'tools':[TOOL]}
        elif method == 'tools/call':
            valid = params.get('name') == TOOL['name'] and params.get('arguments') == {}
            data = {'policy':policy, 'credential_env_present':{name:name in os.environ for name in
                    ('DEEPSEEK_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY')}} if valid else {'error':'invalid_fixture_arguments'}
            result = {'content':[{'type':'text','text':json.dumps(data)}],
                      'structuredContent':data,'isError':not valid}
        else:
            print(json.dumps({'jsonrpc':'2.0','id':request['id'],'error':{'code':-32601,'message':'unsupported_method'}}),flush=True)
            continue
        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
