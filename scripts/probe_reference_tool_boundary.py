"""Controlled item-done vs whole-response experiment; no autonomous model quality."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.reference_cli import reference_command,reference_environment,managed_preferences_status,FIXTURE_TOKEN,REFERENCE_BINARY
from scripts.reference_protocol import serving,final_item
from scripts.probe_reference_cli import request_summary
from agent_lab.coding_tools import CodingTools
from agent_lab.confined_backend import native_python_entry
from agent_lab.engine import Engine,Limits
from agent_lab.model import OpenAIModel
from agent_lab.store import Store
from agent_lab.registry import MCPToolRegistry

PROGRAM="from pathlib import Path\nPath('receipt.txt').write_text('owned-tool-receipt\\n')\nprint('owned-tool-receipt')"


def items_for(kind,root,index,body):
    if index:return [final_item('owned tool stimulus complete')]
    if kind in ('reference_mcp','harness_mcp'):
        def search(entries,namespace=None):
            for entry in entries:
                name=entry.get('name','')
                if entry.get('type')=='namespace':
                    found=search(entry.get('tools',[]),name)
                    if found:return found
                if ('write_receipt' in name or 'owned_fixture/write_receipt' in entry.get('description','')):
                    return name,namespace
        selected=search(body.get('tools',[]))
        if not selected:raise ValueError('owned_mcp_tool_not_advertised')
        name,namespace=selected;args={}
    elif kind=='reference':
        definition=next(item for item in body['tools'] if item.get('name')=='exec_command')
        properties=definition['parameters']['properties']
        args={'cmd':shlex.join([str(native_python_entry()),'-I','-S','-c',PROGRAM])}
        candidates={'workdir':str(root),'login':False,'shell':'/bin/sh','yield_time_ms':1000,'max_output_tokens':1024}
        args.update({key:value for key,value in candidates.items() if key in properties});name='exec_command';namespace=None
    else:
        name='run_command';args={'argv':['python','-I','-S','-c',PROGRAM],'cwd':'.'};namespace=None
    item={'id':'fc_owned_reference','type':'function_call','status':'completed','call_id':'call_owned_reference',
          'name':name,'arguments':json.dumps(args)}
    if namespace:item['namespace']=namespace
    return [item]


def observe_gate(server,receipt,db=None):
    if not server.item_done.wait(6):
        server.release.set();return {'item_done_observed':False,'effect_before_completed':None}
    deadline=time.monotonic()+1.5
    while not receipt.exists() and time.monotonic()<deadline:time.sleep(.01)
    observed=receipt.exists();snapshot={}
    if db is not None:
        with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as connection:
            state=json.loads(connection.execute('SELECT state FROM tasks LIMIT 1').fetchone()[0])
            snapshot={'phase_before_completed':state['phase'],'pending_tool_before_completed':state.get('pending') is not None}
    server.release.set()
    return {'item_done_observed':True,'effect_before_completed':observed,'observation_window_seconds':1.5,**snapshot}


def run_reference(eof,mcp=False):
    base=Path(tempfile.mkdtemp(prefix='agentlab-reference-tool-',dir='/private/tmp'));root=base/'work';root.mkdir()
    (base/'private').write_text('OWNED_PRIVATE_REFERENCE_SENTINEL');receipt=root/'receipt.txt'
    if mcp:shutil.copyfile(Path(__file__).resolve().parents[1]/'tests/fixtures/reference_mcp_server.py',base/'server.py')
    with serving(max_requests=3,build_items=lambda i,b:items_for('reference_mcp' if mcp else 'reference',root,i,b),complete=not eof,completion_gate=True) as server:
        status=managed_preferences_status(base,server.server_port)
        if status['forced_config'] or status['forced_requirements']:return {'blocked':'forced_managed_preferences_present'}
        command=reference_command(base,server.server_port,'Run the owned fixture tool and observe its actual output.',mcp=mcp)
        process=subprocess.Popen(command,cwd=root,env=reference_environment(base),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        gate=observe_gate(server,receipt)
        timed_out=False
        try:stdout,stderr=process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            timed_out=True;process.kill();stdout,stderr=process.communicate(timeout=5)
        summaries=[request_summary(body) for body in server.requests]
        feedback_received=len(server.requests)>1 and any(item.get('type')=='function_call_output' and 'owned-tool-receipt' in str(item.get('output'))
            for item in server.requests[1].get('input',[]))
        result={**gate,'fixture_directory':str(base),'exit_code':process.returncode,'timed_out':timed_out,'stdout':stdout[:262144],'stderr':stderr[:65536],
            'requests':len(server.requests),'request_summaries':summaries,'actual_receipt':receipt.read_text() if receipt.exists() else None,
            'tool_output_returned_in_next_request':feedback_received,'managed_preferences_presence':status,
            'mcp_method_counts':{method:(base/'mcp-methods.txt').read_text().splitlines().count(method) for method in ('initialize','tools/list','tools/call')} if mcp else None,
            'tool_feedback':[{'call_id':item.get('call_id'),'output':str(item.get('output'))[:4096]} for body in server.requests[1:] for item in body.get('input',[]) if item.get('type')=='function_call_output'],
            'tool_declaration_fields':[{key:item[key] for key in ('name','namespace','type') if key in item} for item in server.requests[0].get('tools',[]) if item.get('name')=='exec_command'] if server.requests else []}
    return result


def run_harness(eof,mcp=False):
    base=Path(tempfile.mkdtemp(prefix='agentlab-reference-harness-',dir='/private/tmp'));root=base/'work';root.mkdir();receipt=root/'receipt.txt'
    result={}
    if mcp:
        shutil.copyfile(Path(__file__).resolve().parents[1]/'tests/fixtures/reference_mcp_server.py',base/'server.py')
        config=base/'mcp.json';config.write_text(json.dumps({'servers':[{'name':'owned_fixture','command':[str(native_python_entry()),'-I','-S',str(base/'server.py'),str(receipt),str(base/'mcp-methods.txt')],'cwd':str(base)}]}))
    with serving(max_requests=3,build_items=lambda i,b:items_for('harness_mcp' if mcp else 'harness',root,i,b),complete=not eof,completion_gate=True) as server:
        observer=threading.Thread(target=lambda:result.update(observe_gate(server,receipt,base/'state.sqlite')));observer.start()
        try:
            with patch.dict(os.environ,{'AGENTLAB_REFERENCE_FIXTURE_KEY':FIXTURE_TOKEN}),Store(base/'state.sqlite') as store:
                model=OpenAIModel('reference-protocol-fixture',base_url='http://127.0.0.1:%d/v1'%server.server_port,
                    api_key_env='AGENTLAB_REFERENCE_FIXTURE_KEY',allow_api=True,protocol='responses',stream=True)
                base_tools=CodingTools(root,command_backend='confined-fork')
                tools=MCPToolRegistry(base_tools,config).__enter__() if mcp else base_tools
                engine=Engine(store,tools,model)
                task=engine.create('Run the owned fixture command and observe its actual output.',Limits(max_seconds=25,model_timeout=8,tool_timeout=6,max_steps=8))
                state=engine.run(task);approvals=0
                while state['phase']=='awaiting_approval' and approvals<2:
                    approval=engine.approval_request(task);engine.decide_approval(task,approval['request_id'],approval['digest'],True);approvals+=1
                    state=engine.run(task)
                result.update(phase=state['phase'],reason=state.get('reason'),exact_approvals=approvals,
                    requests=len(server.requests),actual_receipt=receipt.read_text() if receipt.exists() else None,
                    request_summaries=[request_summary(body) for body in server.requests],fixture_directory=str(base),
                    tool_output_returned_in_next_request=len(server.requests)>1 and any(item.get('type')=='function_call_output' and 'owned-tool-receipt' in str(item.get('output'))
                        for item in server.requests[1].get('input',[])))
        finally:
            server.release.set();observer.join(7)
            if mcp and 'tools' in locals():tools.close()
        if mcp:result['mcp_method_counts']={method:(base/'mcp-methods.txt').read_text().splitlines().count(method) for method in ('initialize','tools/list','tools/call')}
    return result


def natural_eof_failure(result):
    if result.get('exit_code')!=1 or result.get('timed_out') is not False:return False
    for line in result.get('stdout','').splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        if (type(event) is dict and event.get('type')=='turn.failed'
                and type(event.get('error')) is dict and 'response.completed' in str(event['error'].get('message',''))):return True
    return False


def probe(eof=False,mcp=False):
    started=time.monotonic();reference=run_reference(eof,mcp);harness=run_harness(eof,mcp)
    observed=all(result.get('item_done_observed') is True for result in (reference,harness))
    if eof:
        passed=(observed and natural_eof_failure(reference) and reference.get('requests')==1 and harness.get('requests')==1
            and reference.get('actual_receipt')=='owned-tool-receipt\n' and harness.get('actual_receipt') is None
            and harness.get('phase')=='needs_confirmation' and harness.get('phase_before_completed')=='model_pending'
            and harness.get('pending_tool_before_completed') is False)
    else:
        passed=(observed and reference.get('exit_code')==0 and reference.get('timed_out') is False and harness.get('phase')=='completed'
            and all(result.get('requests')==2 and result.get('actual_receipt')=='owned-tool-receipt\n'
                    and result.get('tool_output_returned_in_next_request') for result in (reference,harness)))
    repo=Path(__file__).resolve().parents[1]
    return {'tool_transport':'mcp_stdio' if mcp else 'native_command','experiment':'item_done_then_eof' if eof else 'item_done_tool_feedback','passed':passed,
        'reference':reference,'harness':harness,'elapsed_seconds':round(time.monotonic()-started,3),
        'reference_binary_sha256':hashlib.sha256(REFERENCE_BINARY.read_bytes()).hexdigest(),
        'harness_source_sha256':{name:hashlib.sha256((repo/'agent_lab'/name).read_bytes()).hexdigest()
            for name in ('engine.py','model.py','protocols.py','registry.py','coding_tools.py')},
        'harness_effect_authority':'trusted_external_mcp_process' if mcp else 'managed_native_command',
        'harness_effect_is_command_sandboxed':not mcp,'harness_approval_count':harness.get('exact_approvals'),
        'provider_requests':0,'real_llm_task_success_rate':None,'cost_estimate_cny':None,'real_tokens':None,
        'same_as_pinned_source_commit':None,'usage_kind':'synthetic_fixture_counters',
        'owned_mcp_source_sha256':hashlib.sha256((repo/'tests/fixtures/reference_mcp_server.py').read_bytes()).hexdigest() if mcp else None,
        'conditions_differ':(
            ['reference MCP child inherits outer Home/Mach/network boundary; Harness MCP is trusted external with clean env',
             'reference normal command sandbox retained; Harness MCP requires exact approval after completed response, actual count recorded separately',
             'different builtin tools/prompts; identical owned MCP server bytes and 2025-11-25 RPC'] if mcp else
            ['reference outer wrapper prevents nested native sandbox; Harness native backend runs normally',
             'different native authority; command path is diagnostic, not a successful native parity comparison'])}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report',required=True);parser.add_argument('--eof',action='store_true');parser.add_argument('--mcp',action='store_true');args=parser.parse_args()
    target=Path(args.report);target.write_text(json.dumps({'passed':False,'phase':'running'})+'\n')
    try:result={**probe(args.eof,args.mcp),'phase':'completed'}
    except Exception:result={'passed':False,'phase':'failed','error':'reference_tool_probe_failed','real_llm_task_success_rate':None}
    target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({key:value for key,value in result.items() if key not in ('reference','harness')},indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
