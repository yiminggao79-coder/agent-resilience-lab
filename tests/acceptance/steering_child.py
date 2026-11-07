"""Deterministic child Runner used only for cross-process inbox acceptance."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel,final


def main():
    db,root,tid,mode,marker=sys.argv[1:]
    class Model(ScriptedModel):
        def complete(self,*args):
            if mode=='waiting_model':
                Path(marker).write_text('waiting')
                while True:time.sleep(.02)
            return super().complete(*args)
    def hook(event,state):
        if mode=='after_applied' and event=='after_input_applied':
            Path(marker).write_text('committed')
            while True:time.sleep(.02)
    with Store(db) as store:
        state=Engine(store,Tools(root),Model([final('contract complete')]),hook=hook).run(tid)
    print(json.dumps({'pid':__import__('os').getpid(),'phase':state['phase'],'reason':state['reason']}),flush=True)


if __name__=='__main__':main()
