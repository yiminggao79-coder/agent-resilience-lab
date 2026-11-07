"""Existing shared cost authority over synthetic ledgers/non-network clients."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.budget import BudgetLedger

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('synthetic_relocation_probe', REPO/'scripts/probe_budget_relocation.py')
probe_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe_module)


class NonNetworkModel:
    identity = 'synthetic_cli_budget_only'
    calls = []
    def __init__(self, model=None, base_url=None, api_key_env=None, allow_api=False, **options):
        self.model, self.base_url, self.api_key_env, self.allow_api = model, base_url, api_key_env, allow_api
        self.protocol = options.get('protocol', 'chat'); self.token_parameter = options.get('token_parameter', 'max_tokens')
    def complete(self, messages, tools, timeout, max_tokens):
        self.calls.append(os.environ.get('DEEPSEEK_API_KEY') == 'synthetic-loaded')
        return {'message': {'role': 'assistant', 'content': 'Synthetic non-network response'},
                'usage': {'input_tokens': 1, 'output_tokens': 1}, 'finish_reason': 'stop'}


class BudgetRoutingCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve(); self.root = self.base/'work'; self.root.mkdir()
        self.state = self.base/'state.sqlite'; NonNetworkModel.calls = []

    def call(self, ledger, *, load_key=False, env='DEEPSEEK_API_KEY', allow=True):
        out, err = io.StringIO(), io.StringIO()
        def synthetic_loader(): os.environ['DEEPSEEK_API_KEY'] = 'synthetic-loaded'
        with patch('agent_lab.__main__.OpenAIModel', NonNetworkModel), \
             patch('agent_lab.budget.load_first_line_key', side_effect=synthetic_loader) as loader, \
             patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'synthetic-before', 'SYNTHETIC_KEY': 'synthetic-only'}), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            args=['--db',str(self.state),'run','Synthetic','--workspace',str(self.root),
                  '--model','deepseek-flash','--base-url','https://api.deepseek.com',
                  '--api-key-env',env,'--token-parameter','max_tokens','--budget-ledger',str(ledger)]
            if allow: args.append('--allow-api')
            if load_key: args.append('--load-deepseek-key')
            try: code=main(args)
            except SystemExit as error: code=error.code
            restored=os.environ.get('DEEPSEEK_API_KEY')
        return code,out.getvalue(),err.getvalue(),loader.call_count,restored

    def test_actual_relocated_default_does_not_create_ledger_or_delegate(self):
        result=probe_module.probe(); first,second=result['observations']
        self.assertEqual(first['fake_delegate_calls'],0)
        self.assertEqual(second['cli_exit_code'],2)
        self.assertFalse(second['ledger_existed_before']);self.assertFalse(second['ledger_exists_after'])
        self.assertEqual(second['fake_delegate_calls'],0)
        self.assertFalse(result['risk_reproduced'])

    def test_two_runtime_copies_share_explicit_existing_reservations(self):
        ledger_path=self.base/'shared.sqlite';ledger=BudgetLedger(ledger_path)
        for _ in range(14):ledger.reserve('deepseek-flash',1)
        before=ledger_path.read_bytes();mode=ledger_path.stat().st_mode
        for index in (1,2):
            runtime=self.base/('runtime'+str(index));package=runtime/'agent_lab';package.mkdir(parents=True)
            for source in (REPO/'agent_lab').glob('*.py'):shutil.copyfile(source,package/source.name)
            proc=subprocess.run([sys.executable,'-I','-c',probe_module.CHILD,str(runtime),str(self.root),
                str(self.base/('copy-state'+str(index)+'.sqlite')),'empty',str(ledger_path)],
                capture_output=True,text=True,timeout=10,env={'PATH':os.defpath,'LANG':'C.UTF-8'})
            self.assertEqual(proc.returncode,0,proc.stderr)
            result=json.loads(proc.stdout);self.assertEqual(result['cli_exit_code'],2)
            self.assertEqual(result['fake_delegate_calls'],0)
            self.assertFalse((runtime/'.lab/paid-budget.sqlite').exists())
        self.assertEqual(ledger_path.read_bytes(),before);self.assertEqual(ledger_path.stat().st_mode,mode)
        self.assertEqual(len(ledger.report()['requests']),14)

    def test_invalid_missing_inside_or_bad_schema_precedes_key_loader(self):
        malformed=self.base/'malformed.sqlite';malformed.write_bytes(b'SYNTHETIC_PRIVATE_NOT_A_DATABASE')
        inside=self.root/'inside.sqlite';BudgetLedger(inside)
        for ledger in (self.base/'missing.sqlite',malformed,inside):
            with self.subTest(kind=ledger.name):
                code,out,err,loads,_=self.call(ledger,load_key=True)
                self.assertEqual(code,2);self.assertEqual(loads,0)
                self.assertEqual(json.loads(err)['error'],'budget_ledger_unavailable')
                self.assertNotIn('PRIVATE',err);self.assertNotIn(str(self.base),err)
                self.assertEqual(NonNetworkModel.calls,[])
        self.assertFalse((self.base/'missing.sqlite').exists())

    def test_explicit_authorized_synthetic_ledger_and_key_restoration(self):
        ledger_path=self.base/'shared.sqlite';BudgetLedger(ledger_path)
        code,out,err,loads,restored=self.call(ledger_path,load_key=True)
        self.assertEqual(code,0,err);self.assertEqual(loads,1)
        self.assertEqual(NonNetworkModel.calls,[True]);self.assertEqual(restored,'synthetic-before')
        with sqlite3.connect(ledger_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM requests WHERE status=?',('settled',)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT cap_micros FROM budget').fetchone()[0],30000000)
        self.assertNotIn('synthetic-loaded',out+err)

    def test_explicit_ledger_is_not_a_non_deepseek_or_api_disabled_escape(self):
        ledger_path=self.base/'shared.sqlite';ledger=BudgetLedger(ledger_path)
        for options in ({'env':'SYNTHETIC_KEY'},{'allow':False}):
            with self.subTest(options=options):
                code,out,err,loads,_=self.call(ledger_path,**options)
                self.assertEqual(code,2);self.assertEqual(loads,0)
                self.assertEqual(json.loads(err),{'error':'configuration_or_storage_error'})
                self.assertEqual(NonNetworkModel.calls,[])
        self.assertEqual(ledger.report()['requests'],[])


if __name__=='__main__':unittest.main()
