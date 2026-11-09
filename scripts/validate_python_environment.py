"""Real preinstalled pytest under supervised commands; scripted decisions, no API.

Host prepares a compatible external venv and passes its path. This script never
installs packages, loads credentials, opens the paid ledger or copies project data.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from agent_lab import native_jobs


def response(name=None,args=None,number=0):
    message={'role':'assistant','content':'synthetic wiring check'}
    if name:message['tool_calls']=[{'id':'fixture-'+str(number),'type':'function',
        'function':{'name':name,'arguments':json.dumps(args or {})}}]
    return {'message':message,'usage':{'input_tokens':1,'output_tokens':1}}


class Scripted:
    identity='offline_real_pytest_environment_smoke'
    def __init__(self):
        root_command={'argv':['python','-m','pytest','-p','no:cacheprovider','-q'],'cwd':'.'}
        command={'argv':root_command['argv']+['--ignore-glob=.*','--ignore-glob=**/.*'],'cwd':'.'}
        self.replies=iter([response('read_file',{'path':'calc.py'},1),response('run_command',root_command,2),
            response('run_command',command,3),
            response('apply_patch',{'path':'calc.py','old':'return a - b','new':'return a + b'},4),
            response('run_command',command,5),response()])
    def complete(self,*args,**kwargs):return next(self.replies)


def validate(environment):
    environment=Path(environment).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='agentlab-real-pytest-',dir='/private/tmp') as directory:
        base=Path(directory);root=base/'work';root.mkdir();(root/'tests').mkdir()
        (root/'calc.py').write_text('def add(a, b): return a - b\n')
        test=root/'tests'/'test_public.py'
        packages=environment/'lib'/('python%d.%d'%sys.version_info[:2])/'site-packages'
        private=base/'private-sentinel';private.write_text('owned public fixture; must stay outside guest')
        test.write_text('''import os,sys,socket,subprocess,pytest,tomli,calc
from pathlib import Path
@pytest.mark.parametrize('value',[1,2,3])
def test_value(value):
 assert calc.add(value,2)==value+2
 assert tomli.loads('count=3')['count']==3
def test_boundaries():
 assert sys.prefix==%r
 assert os.environ.get('VIRTUAL_ENV')==sys.prefix
 assert os.environ.get('PYTEST_DISABLE_PLUGIN_AUTOLOAD')=='1'
 assert 'DEEPSEEK_API_KEY' not in os.environ
 with pytest.raises(OSError): os.fork()
 with pytest.raises(OSError): subprocess.Popen([sys.executable,'-c','pass'])
 with pytest.raises(OSError): socket.socket().connect(('127.0.0.1',9))
 with pytest.raises(OSError): Path(%r).write_text('bad')
 with pytest.raises(OSError): Path(%r).read_text()
'''%(str(environment),str(packages/'must-not-write'),str(private)))
        before=hashlib.sha256(test.read_bytes()).hexdigest();approved=0
        tools=CodingTools(root,python_environment=environment,read_only_paths=('tests',))
        manifest=tools.command_runtime_policy['python_environment']
        with Store(base/'state.sqlite') as store:
            engine=Engine(store,tools,Scripted())
            tid=engine.create('Public real pytest fail/patch/pass with scripted decisions',
                              Limits(tool_timeout=30,max_seconds=120))
            for _ in range(5):
                state=engine.run(tid)
                if state['phase']!='awaiting_approval':break
                request=engine.approval_request(tid)
                assert request['action']['argv'][:3]==['python','-m','pytest']
                engine.decide_approval(tid,request['request_id'],request['digest'],True);approved+=1
            assert state['phase']=='completed' and approved==3,state.get('reason')
            results=[json.loads(m['content']) for m in state['messages'] if m['role']=='tool']
            native=[r for r in results if 'exit_code' in r]
            assert [r['exit_code'] for r in native]==[2,1,0],native
            assert '.agent-lab.execution.lock' in native[0]['stdout']
            assert '3 failed, 1 passed' in native[1]['stdout'] and '4 passed' in native[2]['stdout']
            assert (root/'calc.py').read_text()=='def add(a, b): return a + b\n'
            assert hashlib.sha256(test.read_bytes()).hexdigest()==before
            assert native_jobs.inspect_workspace(root,base/'state.sqlite')['proof_kind']=='all_native_joined'
            event_data=json.dumps(store.events(tid))
            assert str(environment) not in event_data
        selected={}
        for distribution in importlib.metadata.distributions(path=[str(packages)]):
            name=distribution.metadata['Name']
            selected[name]={'version':distribution.version,
                'requires_python':distribution.metadata.get('Requires-Python'),
                'license':distribution.metadata.get('License-Expression') or distribution.metadata.get('License')}
        return {'kind':'offline_real_pytest_supervised_wiring','decision_model':'scripted',
            'runtime_source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((Path(__file__).resolve().parents[1]/'agent_lab').glob('*.py'))},
            'provider_requests':0,'real_llm_quality_result':False,'credential_file_read':False,
            'original_budget_opened':False,'native_exit_codes':[r['exit_code'] for r in native],
            'final_pytest_cases':4,'exact_command_approvals':approved,'protected_test_bytes_unchanged':True,
            'implicit_root_discovery_rejected_protected_metadata':True,'explicit_hidden_path_exclusions_retry':True,
            'native_quiescent':True,'same_base_venv_prefix_verified':True,
            'package_write_private_read_fork_spawn_network_denied':True,
            'package_files':manifest['packages']['files'],'package_bytes':manifest['packages']['bytes'],
            'package_tree_sha256':manifest['packages']['sha256'],'distributions':selected}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment',required=True,help='host-selected external compatible venv with pytest8/tomli installed')
    print(json.dumps(validate(parser.parse_args().environment),indent=2))
