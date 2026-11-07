"""Synthetic decision Runner owning an actually approved sandboxed process."""
import json
import os
from pathlib import Path
import sys

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from tests.support import ScriptedModel,tool
from tests.dev.test_tool_batches import batch

PROJECT_CODE="""import json,os,time
from pathlib import Path
Path('native-ready.json').write_text(json.dumps({'native_pid':os.getpid(),'supervisor_pid':os.getppid()}))
time.sleep(10)
Path('late').write_text('unexpected late effect')
"""


def main():
    root,db,mode,marker=sys.argv[1:5]
    with Store(db) as store:
        backend=os.environ.get('AGENT_LAB_FIXTURE_COMMAND_BACKEND','nofork')
        tools=CodingTools(root,command_backend=backend)
        project_code=PROJECT_CODE
        if backend=='confined-fork':
            project_code="""import json,os,time
from pathlib import Path
child=os.fork()
if child:
 Path('native-ready.tmp').write_text(json.dumps({'native_pid':os.getpid(),'supervisor_pid':os.getppid(),'descendant_pid':child}))
 os.replace('native-ready.tmp','native-ready.json')
time.sleep(10)
Path('late' if child else 'late-child').write_text('unexpected late effect')
"""
        if mode in ('start','tests'):
            if mode=='tests':
                suite=Path(root)/'tests';suite.mkdir()
                (suite/'test_wait.py').write_text('import unittest\nclass Wait(unittest.TestCase):\n def test_wait(self):\n'+''.join('  '+line+'\n' for line in project_code.splitlines()))
            name,args=('run_tests',{}) if mode=='tests' else ('run_command',{'argv':['python','-c',project_code],'cwd':'.'})
            model=ScriptedModel([batch(tool(name,args,'native'),
                                       tool('write_file',{'path':'tail','content':'never'},'tail'))])
            def observed(event):
                if event['kind']=='tool_started' and event.get('tool') in ('run_tests','run_command'):
                    Path(marker).write_text(json.dumps({'task_id':event['task_id'],'turn_id':event['turn_id'],
                        'runner_pid':os.getpid(),'operation_id':event['operation_id']}))
            engine=Engine(store,tools,model,observer=observed)
            tid=engine.create('Run this approved controlled command',Limits(tool_timeout=15,max_seconds=45))
            if mode=='start':
                waiting=engine.run(tid)
                if waiting['phase']!='awaiting_approval':raise RuntimeError('approval_missing')
                approval=engine.approval_request(tid)
                engine.decide_approval(tid,approval['request_id'],approval['digest'],True)
        else:
            engine=Engine(store,tools,ScriptedModel([]));tid=sys.argv[5]
        state=engine.run(tid)
        print(json.dumps({'phase':state['phase'],'reason':state['reason'],'runner_pid':os.getpid(),
                          'last_tool_cancellation':state.get('last_tool_cancellation')}),flush=True)


if __name__=='__main__':main()
