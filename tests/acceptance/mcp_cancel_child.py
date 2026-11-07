"""New Runner process for durable MCP stop; scripted model, no credentials/API."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.registry import MCPToolRegistry
from agent_lab.store import Store
from tests.support import ScriptedModel,tool


def main():
    mode,root,db,config,ready=sys.argv[1:]
    with Store(db) as store,MCPToolRegistry(CodingTools(root),config) as registry:
        if mode=='run':
            entry=next(item for item in registry.manifest['tools'] if item['tool'] in ('mutate','echo'))
            # Prefer mutate for the stdio fixture; HTTP exposes only echo.
            entry=next((item for item in registry.manifest['tools'] if item['tool']=='mutate'),entry)
            model=ScriptedModel([tool(entry['name'],{'owned':7},'external-effect')])
            engine=Engine(store,registry,model);task=engine.create('Owned MCP stop contract',Limits(tool_timeout=8,max_seconds=30))
            state=engine.run(task)
            assert state['phase']=='awaiting_approval'
            request=engine.approval_request(task);engine.decide_approval(task,request['request_id'],request['digest'],True)
            Path(ready).write_text(json.dumps({'task_id':task,'turn_id':state['turn_id']}))
        else:
            task=json.loads(Path(ready).read_text())['task_id'];engine=Engine(store,registry,ScriptedModel([]))
        state=engine.run(task)
        print(json.dumps({'pid':os.getpid(),'phase':state['phase'],'reason':state.get('reason'),
            'pending_id':state.get('pending',{}).get('operation_id'),'model_calls':state['metrics']['model_calls'],
            'cancellation':state.get('last_tool_cancellation')}),flush=True)


if __name__=='__main__':main()
