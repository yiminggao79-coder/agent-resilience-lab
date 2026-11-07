"""Offline barrier Runner for cross-process cooperative-stop acceptance."""
import json
import os
from pathlib import Path
import signal
import sys
import time

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final


def main():
    root,db,mode,marker,release=sys.argv[1:6]
    def barrier():
        Path(marker).write_text(json.dumps({'task_id':tid,'turn_id':store.load(tid)['turn_id'],'pid':os.getpid()}))
        if mode=='wait_model':
            deadline=time.monotonic()+15
            while not Path(release).exists():
                if time.monotonic()>=deadline:raise RuntimeError('fixture_release_timeout')
                time.sleep(.01)
        else:
            while True:signal.pause()
    class Model(ScriptedModel):
        def complete(self,*args):
            if mode=='wait_model' and not self.seen:barrier()
            return super().complete(*args)
    calls=tool('write_file',{'path':'first','content':'x'})
    calls['message']['tool_calls'] += tool('write_file',{'path':'second','content':'y'})['message']['tool_calls']
    patch='*** Begin Patch\n*** Add File: first\n+x\n*** Add File: second\n+y\n*** End Patch'
    model=Model([final()] if mode=='resume' else [tool('apply_patch_text',{'patch':patch}),final()]
                if mode=='after_patch_effect' else [calls,final()])
    def hook(event,state):
        if mode=='after_patch_effect' and event=='after_tool_effect':barrier()
    with Store(db) as store:
        original=store.save
        def save(state,kind,meta=None,**kwargs):
            original(state,kind,meta,**kwargs)
            if mode=='after_tool_claim' and kind=='tool_started':barrier()
        store.save=save
        engine=Engine(store,CodingTools(root),model,hook)
        tid=sys.argv[6] if mode=='resume' else engine.create('Fixture work',Limits(max_seconds=60,tool_timeout=5))
        state=engine.run(tid)
        print(json.dumps({'phase':state['phase'],'reason':state['reason'],'pid':os.getpid(),
                          'model_calls_this_process':len(model.seen)}),flush=True)


if __name__=='__main__':main()
