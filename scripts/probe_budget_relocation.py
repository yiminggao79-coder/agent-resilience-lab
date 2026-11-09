"""Offline reproduction: copied runtime must not silently reset cost authority.

Copies only public Python runtime source into owned temporary directories. Uses
synthetic credentials, a non-network model and synthetic ledgers exclusively.
Never opens the project's paid ledger or the authorized credential file.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


CHILD = r'''
import contextlib,io,json,os,sqlite3,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from agent_lab import __main__ as cli
from agent_lab.budget import BudgetLedger
from agent_lab.model import ModelError

calls=[]
class NonNetworkModel:
    identity='synthetic_budget_relocation_only'
    def __init__(self,model=None,base_url=None,api_key_env=None,allow_api=False,**options):
        self.model,self.base_url,self.api_key_env,self.allow_api=model,base_url,api_key_env,allow_api
        self.protocol=options.get('protocol','chat')
        self.token_parameter=options.get('token_parameter','max_tokens')
    def complete(self,messages,tools,timeout,max_tokens):
        calls.append(True)
        return {'message':{'role':'assistant','content':'synthetic response; no network'},
                'usage':{'input_tokens':1,'output_tokens':1},'finish_reason':'stop'}

runtime=Path(sys.argv[1]);workspace=Path(sys.argv[2]);state=Path(sys.argv[3]);seed=sys.argv[4]=='seed'
ledger_path=runtime/'.lab'/'paid-budget.sqlite'
if seed:
    ledger=BudgetLedger(ledger_path)
    for _ in range(14):ledger.reserve('deepseek-flash',1)
existed_before=ledger_path.exists()
cli.OpenAIModel=NonNetworkModel
os.environ['DEEPSEEK_API_KEY']='synthetic-not-a-real-key'
output,errors=io.StringIO(),io.StringIO()
with contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
    arguments=['--db',str(state),'run','Synthetic offline budget check','--workspace',str(workspace),
        '--model','deepseek-flash','--base-url','https://api.deepseek.com',
        '--api-key-env','DEEPSEEK_API_KEY','--token-parameter','max_tokens','--allow-api']
    if len(sys.argv)>5:arguments+=['--budget-ledger',sys.argv[5]]
    code=cli.main(arguments)
exists_after=ledger_path.exists()
count=None
if exists_after:
    with sqlite3.connect('file:'+str(ledger_path)+'?mode=ro',uri=True) as db:
        count=db.execute('SELECT count(*) FROM requests').fetchone()[0]
print(json.dumps({'seeded_synthetic_existing_ledger':seed,'ledger_existed_before':existed_before,
    'ledger_exists_after':exists_after,'cli_exit_code':code,'fake_delegate_calls':len(calls),
    'synthetic_request_records':count,'provider_requests_sent':0,
    'credential_file_read':False,'real_project_ledger_opened':False}))
'''


def probe():
    source = Path(__file__).resolve().parents[1] / 'agent_lab'
    observations = []
    with tempfile.TemporaryDirectory(prefix='agent-lab-budget-relocation-') as directory:
        base = Path(directory); workspace = base / 'work'; workspace.mkdir()
        for index, seed in enumerate(('seed', 'empty')):
            runtime = base / ('runtime-' + str(index)); package = runtime / 'agent_lab'; package.mkdir(parents=True)
            for file in source.glob('*.py'):
                if not file.is_file() or file.is_symlink(): raise RuntimeError('unexpected_runtime_source')
                shutil.copyfile(file, package / file.name)
            result = subprocess.run([sys.executable, '-I', '-c', CHILD, str(runtime), str(workspace),
                str(base / ('task-' + str(index) + '.sqlite')), seed], capture_output=True, text=True, timeout=10,
                env={'PATH': os.defpath, 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'})
            if result.returncode: raise RuntimeError('synthetic_probe_failed')
            observations.append(json.loads(result.stdout))
    return {'kind': 'offline_synthetic_runtime_relocation', 'observations': observations,
            'real_provider_or_billing_result': False,
            'risk_reproduced': observations[0]['fake_delegate_calls'] == 0
                and observations[1]['fake_delegate_calls'] > 0
                and not observations[1]['ledger_existed_before']}


if __name__ == '__main__': print(json.dumps(probe(), indent=2))
