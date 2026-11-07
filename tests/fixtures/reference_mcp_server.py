"""Synthetic owned effect for comparing dispatch boundaries, not task solving."""
import json
from pathlib import Path
import sys

receipt=Path(sys.argv[1]);log=Path(sys.argv[2])
while True:
    raw=sys.stdin.readline(65537)
    if not raw:break
    if len(raw)>65536:raise SystemExit(2)
    request=json.loads(raw);method=request.get('method')
    with log.open('a') as output:output.write(str(method)+'\n')
    if method and method.startswith('notifications/'):continue
    if method=='initialize':
        result={'protocolVersion':'2025-11-25','serverInfo':{'name':'owned-boundary-fixture','version':'1'},'capabilities':{'tools':{}}}
    elif method=='ping':result={}
    elif method=='tools/list':
        result={'tools':[{'name':'write_receipt','description':'Synthetic dispatch-boundary probe: append one receipt only in the owned temporary fixture.',
            'inputSchema':{'type':'object','properties':{},'additionalProperties':False},
            'annotations':{'readOnlyHint':False,'destructiveHint':False,'idempotentHint':False,'openWorldHint':False}}]}
    elif method=='tools/call' and request.get('params',{}).get('name')=='write_receipt' and request['params'].get('arguments',{})=={}:
        with receipt.open('a') as output:output.write('owned-tool-receipt\n')
        result={'content':[{'type':'text','text':'owned-tool-receipt'}],'isError':False}
    else:
        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'error':{'code':-32601,'message':'Owned fixture rejects unsupported method'}}),flush=True);continue
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
