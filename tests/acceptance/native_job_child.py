"""Owned, short-lived native effect fixture for supervisor-death quarantine."""
import json
from pathlib import Path
import sys
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from tests.support import ScriptedModel,tool

CODE="""import json,os,time
from pathlib import Path
Path('ready.json').write_text(json.dumps({'native_pid':os.getpid(),'supervisor_pid':os.getppid()}))
end=time.monotonic()+3
while time.monotonic()<end:
 if Path('release').exists(): break
 if Path('trigger-old-effect').exists():
  Path('old-effect').write_text('old native process still active')
  break
 time.sleep(.01)
"""


def main():
    root,database,marker=sys.argv[1:]
    with Store(database) as store:
        engine=Engine(store,CodingTools(root),ScriptedModel([tool('run_command',{'argv':['python','-c',CODE],'cwd':'.'},'native')]))
        tid=engine.create('Controlled native job',Limits(tool_timeout=10,max_seconds=30))
        assert engine.run(tid)['phase']=='awaiting_approval'
        approval=engine.approval_request(tid)
        engine.decide_approval(tid,approval['request_id'],approval['digest'],True)
        state=store.load(tid)
        Path(marker).write_text(json.dumps({'task_id':tid,'turn_id':state['turn_id'],'operation_id':state['pending']['operation_id']}))
        state=engine.run(tid)
        print(json.dumps({'phase':state['phase'],'reason':state['reason']}),flush=True)


if __name__=='__main__':main()
