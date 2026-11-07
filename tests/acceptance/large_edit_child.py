"""Offline contract child: use the actual read-lines digest, then edit locally."""
import json
from pathlib import Path
import signal
import sys

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from tests.acceptance.protocol_fixture import ProtocolFixture,feedback,finish,function_call


def main():
    root,db,mode=sys.argv[1:4]
    def edit_from_feedback(messages):
        result=feedback(messages)
        return function_call('edit_file',{'path':'large.py','expected_sha256':result['sha256'],
                             'edits':[{'old':'VALUE = 1','new':'VALUE = 2'},
                                      {'old':'END = "old"','new':'END = "new"'}]})
    decisions=([function_call('read_lines',{'path':'large.py','start_line':1,'end_line':3}),
                edit_from_feedback,finish()] if mode=='crash' else [finish()])
    def hook(event,state):
        if mode=='crash' and event=='after_tool_effect' and state['pending']['intent']['name']=='edit_file':
            print(json.dumps({'task_id':state['id'],'pid':__import__('os').getpid()}),flush=True)
            while True:signal.pause()
    with Store(Path(db)) as store:
        engine=Engine(store,CodingTools(root),ProtocolFixture(decisions),hook)
        tid=engine.create('Apply version-bound edits',Limits(max_seconds=60,tool_timeout=5)) if mode=='crash' else sys.argv[4]
        state=engine.run(tid)
        print(json.dumps({'phase':state['phase'],'reason':state['reason'],'pid':__import__('os').getpid()}),flush=True)


if __name__=='__main__':main()
