"""Owned resource callbacks using a pinned official third-party SDK, not LLM quality."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.mcp import MCPClient
from agent_lab.registry import MCPToolRegistry
from agent_lab.store import Store
from scripts.probe_official_mcp import ProtocolModel,tool


def probe(node,install):
    node=Path(node).resolve();install=Path(install).resolve();repo=Path(__file__).resolve().parents[1]
    package=install/'node_modules/@modelcontextprotocol/server'
    metadata=json.loads((package/'package.json').read_text())
    if metadata.get('name')!='@modelcontextprotocol/server' or metadata.get('version')!='2.4.0':raise ValueError('pinned_sdk_required')
    lock=(install/'package-lock.json').read_bytes()
    if lock!=(repo/'evidence/interop/filesystem-2.2.0/package-lock.json').read_bytes():raise ValueError('pinned_lock_required')
    source=repo/'tests/fixtures/mcp_sdk_resources.mjs';started=time.monotonic()
    command=(str(node),str(source),str(install))
    with tempfile.TemporaryDirectory(prefix='agentlab-sdk-resource-probe-',dir='/private/tmp') as directory:
        base=Path(directory);root=base/'work';root.mkdir()
        with MCPClient(command,base,timeout=5,max_bytes=65536) as client:
            version=client.protocol_version
            assert client.supports('resources')
            resources=client.list_resources();templates=client.list_resource_templates()
            assert {r['uri'] for r in resources['resources']}=={'owned:///reference','owned:///binary'}
            assert templates['resourceTemplates'][0]['uriTemplate']=='owned:///reference/{name}'
            assert client.read_resource('owned:///reference/item')['contents'][0]['text']=='Named SDK reference item'
            assert client.read_resource('owned:///binary')['contents'][0]['blob']=='YWJjAA=='
        config=base/'mcp.json';config.write_text(json.dumps({'servers':[{'name':'sdk','command':list(command),'cwd':str(base),'timeout':5}]}))
        with Store(base/'state.sqlite') as store,MCPToolRegistry(CodingTools(root),config) as registry:
            model=ProtocolModel([tool('list_mcp_resources',{'server':'sdk'},'list'),
                tool('list_mcp_resource_templates',{'server':'sdk'},'templates'),
                tool('read_mcp_resource',{'server':'sdk','uri':'owned:///reference/item'},'text'),
                tool('read_mcp_resource',{'server':'sdk','uri':'owned:///binary'},'binary'),
                {'message':{'role':'assistant','content':'protocol complete'},'usage':{'input_tokens':100,'output_tokens':20}}])
            engine=Engine(store,registry,model);tid=engine.create('Exercise SDK resource context',Limits(max_steps=12,max_seconds=40,tool_timeout=5))
            state=engine.run(tid);approvals=0
            while state['phase']=='awaiting_approval':
                request=engine.approval_request(tid);engine.decide_approval(tid,request['request_id'],request['digest'],True)
                approvals+=1;state=engine.run(tid)
            assert state['phase']=='completed' and approvals==4
            feedback=[json.loads(m['content']) for m in state['messages'] if m['role']=='tool']
            assert feedback[2]['contents'][0]['text']=='Named SDK reference item'
            assert feedback[3]['contents'][0]['blob_bytes']==4 and 'blob' not in feedback[3]['contents'][0]
            assert 'Named SDK reference item' not in json.dumps(store.events(tid))
    return {'passed':True,'phase':'completed','experiment':'official_sdk_owned_resource_interoperability',
        'sdk_version':'2.4.0','protocol_version':version,'exact_approvals':approvals,'static_resources':len(resources['resources']),
        'templates':len(templates['resourceTemplates']),'template_read':True,'binary_metadata_only':True,
        'elapsed_seconds':round(time.monotonic()-started,3),'package_lock_sha256':hashlib.sha256(lock).hexdigest(),
        'fixture_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'model_kind':ProtocolModel.identity,
        'provider_requests':0,'real_llm_task_success_rate':None,'real_tokens':None,'cost_estimate_cny':None,
        'published_server_product':False,'external_server_is_command_sandboxed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('node','install','report'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();target=Path(args.report)
    target.write_text(json.dumps({'passed':False,'phase':'running','real_llm_task_success_rate':None})+'\n')
    try:
        if not __debug__:raise RuntimeError('verification_requires_assertions_enabled')
        result=probe(args.node,args.install);code=0
    except Exception:
        result={'passed':False,'phase':'failed','error':'resource_interop_probe_failed','real_llm_task_success_rate':None};code=1
    target.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print(json.dumps(result,indent=2,sort_keys=True));return code


if __name__=='__main__':raise SystemExit(main())
