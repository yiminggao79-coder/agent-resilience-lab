"""Independent routing/shared admission using synthetic ledgers exclusively."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import __main__ as cli
from agent_lab.budget import BudgetLedger, BudgetExceeded, INPUT_CEILING, RATES
from agent_lab.engine import Engine
from agent_lab.model import OpenAIModel


CHILD = '''import json,select,sys
sys.path.insert(0,sys.argv[1])
from agent_lab.budget import BudgetLedger,BudgetExceeded
ledger=BudgetLedger.open_existing(sys.argv[2],workspace=sys.argv[3])
print('ready',flush=True)
if not select.select([sys.stdin],[],[],5)[0]:raise SystemExit(3)
sys.stdin.readline()
try:
 request=ledger.reserve('deepseek-flash',1)
except BudgetExceeded:
 print(json.dumps({'outcome':'blocked'}),flush=True)
else:
 ledger.settle(request,None)
 print(json.dumps({'outcome':'unknown_held','request_id':request}),flush=True)
'''


class BudgetRoutingReviewTests(unittest.TestCase):
    def test_two_actual_processes_share_one_remaining_reservation_without_release(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory).resolve(); workspace=base/'work'; workspace.mkdir()
            path=base/'synthetic-budget.sqlite'; seed=BudgetLedger(path)
            seeded=[]
            for _ in range(13):
                request=seed.reserve('deepseek-flash',1); seed.settle(request,None); seeded.append(request)
            repo=Path(__file__).resolve().parents[2]
            processes=[]
            try:
                for _ in range(2):
                    process=subprocess.Popen([sys.executable,'-I','-c',CHILD,str(repo),str(path),str(workspace)],
                        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                        env={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'})
                    processes.append(process)
                for process in processes:
                    self.assertTrue(select.select([process.stdout],[],[],3)[0],'synthetic child admission timed out')
                    self.assertEqual(process.stdout.readline(),'ready\n')
                for process in processes:
                    process.stdin.write('reserve\n'); process.stdin.flush()
                results=[]
                for process in processes:
                    output,error=process.communicate(timeout=5)
                    self.assertEqual(process.returncode,0,error)
                    results.append(json.loads(output))
                self.assertEqual(sorted(item['outcome'] for item in results),['blocked','unknown_held'])
            finally:
                for process in processes:
                    if process.poll() is None: process.kill()
                    process.communicate(timeout=2)
            reopened=BudgetLedger.open_existing(path,workspace=workspace)
            report=reopened.report()
            self.assertEqual(len(report['requests']),14)
            self.assertEqual(report['unresolved_requests'],14)
            self.assertTrue(all(item['status']=='unknown' for item in report['requests']))
            self.assertTrue(set(seeded)<={item['id'] for item in report['requests']})
            quote=INPUT_CEILING*RATES['deepseek-flash'][0]+RATES['deepseek-flash'][1]
            self.assertAlmostEqual(report['committed_upper_cny'],14*quote/1000000)
            with self.assertRaises(BudgetExceeded): reopened.reserve('deepseek-flash',1)

    def test_unapproved_endpoint_cannot_load_key_reserve_or_create_task(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory).resolve(); workspace=base/'work'; workspace.mkdir()
            ledger_path=base/'synthetic-budget.sqlite'; BudgetLedger(ledger_path)
            before=(ledger_path.read_bytes(),ledger_path.stat().st_mode)
            state=base/'state.sqlite'
            for endpoint in ('https://unapproved.invalid/v1','https://api.deepseek.com/extra'):
                out,err=io.StringIO(),io.StringIO()
                with self.subTest(endpoint=endpoint), \
                     patch.dict(os.environ,{'DEEPSEEK_API_KEY':'synthetic-before'}), \
                     patch('agent_lab.budget.load_first_line_key',side_effect=AssertionError('no credential loader')) as loader, \
                     patch.object(BudgetLedger,'reserve',side_effect=AssertionError('no reservation')) as reserve, \
                     patch.object(OpenAIModel,'complete',side_effect=AssertionError('no provider')) as complete, \
                     patch.object(Engine,'create',side_effect=AssertionError('no task')) as create, \
                     redirect_stdout(out),redirect_stderr(err):
                    code=cli.main(['--db',str(state),'run','Synthetic admission only','--workspace',str(workspace),
                        '--model','deepseek-flash','--base-url',endpoint,'--api-key-env','DEEPSEEK_API_KEY',
                        '--allow-api','--load-deepseek-key','--budget-ledger',str(ledger_path)])
                    self.assertEqual(os.environ['DEEPSEEK_API_KEY'],'synthetic-before')
                self.assertEqual(code,2)
                self.assertEqual(out.getvalue(),'')
                self.assertEqual(json.loads(err.getvalue()),{'error':'configuration_or_storage_error'})
                for operation in (loader,reserve,complete,create): operation.assert_not_called()
                self.assertEqual((ledger_path.read_bytes(),ledger_path.stat().st_mode),before)
            with sqlite3.connect(state) as database:
                self.assertEqual(database.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
