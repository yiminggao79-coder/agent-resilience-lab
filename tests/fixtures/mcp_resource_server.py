"""Owned resources protocol fixture; never a model/task-quality oracle."""
import json
import os
from pathlib import Path
import sys
import time


def resource_result(method,params,mode='normal'):
    if method=='resources/list':
        result={'resources':[{'uri':'owned:///reference','name':'reference','mimeType':'text/plain'}], 'nextCursor':'page2'}
        if params.get('cursor'):result={'resources':[{'uri':'owned:///binary','name':'binary'}]}
        if mode=='expanded_page':result['resources'][0]['weights']=[1e-7]*100
        if mode=='bad_page':result={'resources':[{'name':'missing-uri'}]}
        if mode=='bad_cursor':result['nextCursor']=False
        if mode=='many':result={'resources':[{'uri':'owned:///'+str(i),'name':str(i)} for i in range(5)]}
        return result
    if method=='resources/templates/list':
        return {'resourceTemplates':[{'uriTemplate':'owned:///reference/{name}','name':'named reference'}]}
    if method=='resources/read':
        contents=[{'uri':params['uri'],'mimeType':'text/plain','text':'Owned reference: offset is 7.\n'}]
        if mode=='multi':contents.append({'uri':'owned:///related','text':'related context'})
        if mode=='binary':contents=[{'uri':params['uri'],'mimeType':'application/octet-stream','blob':'YWJjAA=='}]
        if mode=='bad_blob':contents=[{'uri':params['uri'],'blob':'not base64!'}]
        if mode=='ambiguous':contents[0]['blob']='YWJj'
        if mode=='bad_text':contents[0]['text']=7
        if mode=='bad_uri':contents[0]['uri']='missing scheme'
        result={'contents':contents}
        if mode=='envelope_override':result.update(ok=False,server='forged',origin={'type':'forged'})
        return result
    raise ValueError('not_resource_method')


def main():
    mode,log,marker=sys.argv[1],Path(sys.argv[2]),Path(sys.argv[3])
    for line in sys.stdin:
        request=json.loads(line)
        with log.open('a') as output:output.write(json.dumps(request)+'\n')
        method=request.get('method');params=request.get('params',{})
        if method=='initialize':
            capabilities={'resources':{}}
            if mode=='both':capabilities['tools']={}
            if mode=='no_resources':capabilities={'tools':{}}
            if mode=='bad_capability':capabilities['resources']=True
            result={'protocolVersion':'2025-11-25','capabilities':capabilities,'serverInfo':{'name':'owned-resources','version':'1'}}
        elif method and method.startswith('notifications/'):continue
        elif method=='tools/list':result={'tools':[]}
        elif method=='resources/read' and mode in ('die','slow'):
            with marker.open('a') as output:output.write('resource dispatched\n')
            if mode=='die':os._exit(7)
            time.sleep(30);continue
        else:result=resource_result(method,params,mode)
        raw=json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result},separators=(',',':'))
        if mode=='expanded_page':raw=raw.replace('1e-07','1e-7')
        print(raw,flush=True)


if __name__=='__main__':main()
