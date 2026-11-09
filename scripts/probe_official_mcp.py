"""Explicit pinned third-party stdio interoperability, never an LLM quality score.

Install external dependencies separately with lifecycle scripts disabled. This
script downloads nothing and neither reads credentials nor enables a provider.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_lab import native_jobs
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.mcp import MCPClient,MCPError
from agent_lab.registry import MCPToolRegistry
from agent_lab.store import Store


def sha(data):return hashlib.sha256(data).hexdigest()


class ProtocolModel:
    identity='offline_scripted_protocol_double'
    def __init__(self,responses):self.responses=iter(responses)
    def complete(self,messages,tools,timeout,max_tokens):return next(self.responses)


def tool(name,args,id):
    return {'message':{'role':'assistant','content':None,'tool_calls':[
        {'id':id,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]},'usage':{'input_tokens':100,'output_tokens':40}}


def probe(node,install):
    node=Path(node).resolve(strict=True);install=Path(install).resolve(strict=True)
    package=install/'node_modules/@modelcontextprotocol/server-filesystem'
    metadata=json.loads((package/'package.json').read_text())
    if metadata.get('name')!='@modelcontextprotocol/server-filesystem' or metadata.get('version')!='2.2.0':raise ValueError('pinned_server_required')
    server=package/'dist/index.js';lock=json.loads((install/'package-lock.json').read_text())
    report={'experiment':'official_filesystem_stdio_interoperability','server_package':metadata['name'],'server_version':metadata['version'],
        'model_kind':ProtocolModel.identity,'real_llm_task_success_rate':None,'real_tokens':None,'cost_estimate_cny':None,
        'package_lock_sha256':sha((install/'package-lock.json').read_bytes()),'entry_sha256':sha(server.read_bytes()),
        'packages':[{'package':name.removeprefix('node_modules/'),'version':item.get('version'),'license_metadata':item.get('license')}
                    for name,item in sorted(lock['packages'].items()) if name],
        'external_server_is_command_sandboxed':False,'provider_requests':0}
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='agentlab-official-interop-',dir='/private/tmp') as directory:
        base=Path(directory);boundary=base/'boundary';boundary.mkdir()
        (boundary/'value').write_text('owned interop value');private=base/'private';private.write_text('owned private sentinel')
        (boundary/'outside-link').symlink_to(private)
        def client(read_only=False):return MCPClient((str(node),str(server),*(['--read-only'] if read_only else []),'--confine-roots',str(boundary)),base,timeout=8,max_bytes=65536,max_tools=32)
        with_client=client()
        try:
            report['initialize']=with_client.start();catalog=with_client.list_tools();report['catalog_names']=[item['name'] for item in catalog]
            read=with_client.call_tool('read_text_file',{'path':str(boundary/'value')})
            assert read['structuredContent']['content']=='owned interop value'
            denied={}
            for name,path in (('outside',private),('symlink',boundary/'outside-link')):
                result=with_client.call_tool('read_text_file',{'path':str(path)})
                denied[name]=result.get('isError') is True
                assert denied[name] and 'owned private sentinel' not in json.dumps(result)
            result=with_client.call_tool('write_file',{'path':str(private),'content':'unexpected'})
            assert result.get('isError') is True and private.read_text()=='owned private sentinel'
            report['boundaries']={**denied,'outside_write_denied':True,'private_bytes_unchanged':True}
        finally:with_client.close()
        readonly=client(True)
        try:
            readonly.start();names={item['name'] for item in readonly.list_tools()}
            assert 'read_text_file' in names and not names.intersection({'write_file','append_file','edit_file','create_directory','move_file','copy_file'})
            report['readonly_catalog_names']=sorted(names)
            try:readonly.call_tool('write_file',{'path':str(boundary/'value'),'content':'unexpected'})
            except MCPError as error:assert error.code=='tool_not_discovered'
            else:raise AssertionError('readonly_write_discovered')
        finally:readonly.close()

        root=base/'coding';root.mkdir();(root/'calc.py').write_text('def add(a,b):\n return a-b\n')
        original_test='import unittest\nfrom calc import add\nclass Check(unittest.TestCase):\n def test_add(self):\n  self.assertEqual(add(2,3),5)\n'
        (root/'test_calc.py').write_text(original_test)
        config=base/'mcp.json';config.write_text(json.dumps({'servers':[{'name':'official','command':[str(node),str(server),'--confine-roots',str(root)],'cwd':str(base),'timeout':8}]}))
        db=base/'state.sqlite'
        with Store(db) as store,MCPToolRegistry(CodingTools(root),config) as registry:
            names={entry['tool']:entry['name'] for entry in registry.manifest['tools']}
            model=ProtocolModel([tool('run_tests',{},'before'),tool(names['list_directory'],{'path':str(root)},'list'),
                tool(names['read_text_file'],{'path':str(root/'calc.py')},'read'),
                tool(names['edit_file'],{'path':str(root/'calc.py'),'edits':[{'oldText':'return a-b','newText':'return a+b'}]},'edit'),
                tool('run_tests',{},'after'),{'message':{'role':'assistant','content':'protocol complete'},'usage':{'input_tokens':100,'output_tokens':20}}])
            engine=Engine(store,registry,model);task=engine.create('Repair owned project through official MCP tools and verify native tests',Limits(max_steps=16,max_seconds=120,tool_timeout=8))
            approvals=0
            for _ in range(5):
                state=engine.run(task)
                if state['phase']!='awaiting_approval':break
                request=engine.approval_request(task);engine.decide_approval(task,request['request_id'],request['digest'],True);approvals+=1
            assert state['phase']=='completed',state.get('reason')
            results=[json.loads(message['content']) for message in state['messages'] if message['role']=='tool']
            native=[r['exit_code'] for r in results if 'exit_code' in r]
            assert native==[1,0],native
            assert approvals==3 and all(result['ok'] for result in results[1:4])
            assert (root/'test_calc.py').read_text()==original_test
            assert (root/'calc.py').read_text()=='def add(a,b):\n return a+b\n'
            assert native_jobs.inspect_workspace(root,db)['proof_kind']=='all_native_joined'
            # Independent new native worker observation of the final artifact,
            # not the assistant final text and not a task holdout score.
            fresh=CodingTools(root);verification=fresh.execute(fresh.prepare('run_tests',{}),8)
            assert verification['exit_code']==0,verification
            assert native_jobs.inspect_workspace(root,db)['proof_kind']=='all_native_joined'
            report['engine']={'phase':state['phase'],'mcp_exact_approvals':approvals,'native_test_exit_codes':native,
                'fresh_native_verification_exit':verification['exit_code'],'original_test_sha256':sha(original_test.encode()),
                'test_bytes_unchanged_observed':True,'host_readonly_policy_enforced_for_mcp':False,
                'final_source_sha256':sha((root/'calc.py').read_bytes()),'native_proof':'all_native_joined',
                'mcp_tools_used':['list_directory','read_text_file','edit_file'],'model_calls':state['metrics']['model_calls']}
        report.update(passed=True,elapsed_seconds=round(time.monotonic()-started,3))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--node',required=True);parser.add_argument('--install',required=True);parser.add_argument('--report',required=True)
    args=parser.parse_args()
    target=Path(args.report)
    target.write_text(json.dumps({'passed':False,'phase':'running','real_llm_task_success_rate':None})+'\n')
    try:
        if not __debug__:raise RuntimeError('verification_requires_assertions_enabled')
        result={**probe(args.node,args.install),'phase':'completed'}
        code=0
    except Exception:
        # Never let an old successful report survive a failed rerun or echo
        # arbitrary third-party error text. A running marker is already false.
        result={'passed':False,'phase':'failed','error':'interop_probe_failed','real_llm_task_success_rate':None}
        code=1
    target.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps(result,indent=2,sort_keys=True))
    return code


if __name__=='__main__':raise SystemExit(main())
