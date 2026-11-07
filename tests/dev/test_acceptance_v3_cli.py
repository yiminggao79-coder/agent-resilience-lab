"""No provider calls: preflight/once policy via synthetic SDK and temp ledger."""
from contextlib import redirect_stdout,redirect_stderr
import io
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.budget import BudgetLedger
from scripts import evaluate_acceptance_v3 as runner
from tests.support import ScriptedModel,tool,final


class AcceptanceV3CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.ledger=BudgetLedger(self.root/'budget.sqlite')
        self.snapshot={'runtime_dirty':False,'commit':'synthetic_contract_only','source_sha256':{}}
        self.report={'cap_cny':30,'committed_upper_cny':1,'unresolved_requests':0,'halted':False}

    def call(self,*args):
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):code=runner.main(list(args))
        return code,json.loads(out.getvalue()),err.getvalue()

    def test_blocked_budget_precedes_key_model_and_holdout_body_access(self):
        self.assertTrue(hasattr(runner,'main'),'V3 CLI entry is missing')
        blocked={**self.report,'committed_upper_cny':29.410374,'unresolved_requests':6}
        with patch.object(runner,'implementation_snapshot',return_value=self.snapshot,create=True),patch.object(runner,'BudgetLedger',return_value=self.ledger,create=True),patch.object(self.ledger,'report',return_value=blocked),patch.object(runner,'load_first_line_key',side_effect=AssertionError('no key'),create=True),patch.object(runner,'OpenAIModel',side_effect=AssertionError('no model'),create=True),patch.object(runner,'_load_suite',side_effect=AssertionError('no holdout body'),create=True):
            code,result,err=self.call('--allow-api')
        self.assertEqual(code,2,err);self.assertEqual(result['blocked'],'budget_unavailable')
        self.assertEqual(result['api_requests_sent'],0)
        self.assertEqual(result['budget_preflight']['unresolved_requests'],6)

    def test_dirty_runtime_precedes_budget_and_key_access(self):
        self.assertTrue(hasattr(runner,'main'))
        with patch.object(runner,'implementation_snapshot',return_value={**self.snapshot,'runtime_dirty':True},create=True),patch.object(runner,'BudgetLedger',side_effect=AssertionError('no ledger'),create=True),patch.object(runner,'load_first_line_key',side_effect=AssertionError('no key'),create=True):
            code,result,err=self.call('--allow-api')
        self.assertEqual(code,2,err);self.assertEqual(result['blocked'],'commit_runtime_before_acceptance')
        self.assertEqual(result['api_requests_sent'],0)

    def test_budget_storage_fault_is_fixed_diagnostic_before_key_or_task(self):
        with patch.object(runner,'implementation_snapshot',return_value=self.snapshot),patch.object(runner,'BudgetLedger',side_effect=sqlite3.OperationalError('PRIVATE_DATABASE_PATH')),patch.object(runner,'load_first_line_key',side_effect=AssertionError('no key')),patch.object(runner,'_load_suite',side_effect=AssertionError('no task')):
            code,result,err=self.call('--allow-api')
        self.assertEqual(code,2);self.assertEqual(err,'')
        self.assertEqual(result,{'blocked':'preflight_unavailable','api_requests_sent':0})

    def test_default_is_permission_preflight_without_exposure_or_suite_read(self):
        self.assertTrue(hasattr(runner,'main'))
        with patch.object(runner,'implementation_snapshot',return_value=self.snapshot,create=True),patch.object(runner,'BudgetLedger',return_value=self.ledger,create=True),patch.object(runner,'load_first_line_key',side_effect=AssertionError('no key'),create=True),patch.object(runner,'_load_suite',side_effect=AssertionError('no task'),create=True):
            code,result,err=self.call()
        self.assertEqual(code,0,err);self.assertEqual(result['mode'],'preflight_only')
        self.assertTrue(result['budget_allows_request']);self.assertFalse(result['suite_checked'])
        self.assertEqual(result['api_requests_sent'],0)
        self.assertEqual(self.ledger.report()['requests'],[])

    def test_synthetic_stack_once_gate_and_changed_identity_are_distinct(self):
        self.assertTrue(hasattr(runner,'main'))
        source=self.root/'suite'/'project';source.mkdir(parents=True)
        (source/'solve.py').write_text('def combine(a,b):\n return a-b\n')
        (source/'test_api.py').write_text('import unittest\nfrom solve import combine\nclass T(unittest.TestCase):\n def test_it(self):\n  self.assertEqual(combine(2,3),5)\n')
        case={'id':'synthetic_addition','task':'PRIVATE_SYNTHETIC_TARGET','workspace_dir':'project',
              'immutable_files':['test_api.py'],
              'oracle':{'module':'solve','function':'combine','inputs':[[3,4]],'expected':[7]}}
        model=ScriptedModel([tool('write_file',{'path':'solve.py','content':'def combine(a,b):\n return a+b\n'},'fix'),final('done')])
        model.model='deepseek-flash';model.base_url='https://api.deepseek.com';model.api_key_env='DEEPSEEK_API_KEY'
        model.protocol='chat';model.token_parameter='max_tokens';model.allow_api=True
        original_key=os.environ.get('DEEPSEEK_API_KEY')
        def fake_load():os.environ['DEEPSEEK_API_KEY']='synthetic-only-not-a-real-key'
        with patch.object(runner,'REPO',self.root),patch.object(runner,'implementation_snapshot',return_value=self.snapshot,create=True),patch.object(runner,'BudgetLedger',return_value=self.ledger,create=True),patch.object(runner,'_load_suite',return_value=('1'*64,[case]),create=True) as suite_loader,patch.object(runner,'verify_freeze',return_value='1'*64,create=True) as freeze_checker,patch.object(runner,'_probe_backend',return_value=True,create=True),patch.object(runner,'load_first_line_key',side_effect=fake_load,create=True) as loader,patch.object(runner,'OpenAIModel',return_value=model,create=True) as sdk:
            code,first,err=self.call('--allow-api','--suite',str(self.root/'suite'))
            self.assertEqual(code,0,err);self.assertEqual(first['quality_measured'],1);self.assertEqual(first['quality_passes'],1)
            self.assertTrue(first['evaluation_valid'])
            code,second,err=self.call('--allow-api','--suite',str(self.root/'suite'))
            self.assertEqual(code,2,err);self.assertEqual(second['quality_measured'],0)
            self.assertEqual(second['blocked_prior_exposure'],1)
            self.assertEqual(sdk.call_count,1);self.assertEqual(loader.call_count,1)
            # A different synthetic suite identity may run, but a changed
            # post-run freeze must not publish a valid independent pass rate.
            suite_loader.return_value=('2'*64,[case]);freeze_checker.return_value='3'*64
            model.responses=iter([tool('write_file',{'path':'solve.py','content':'def combine(a,b):\n return a+b\n'},'fix-again'),final('done')])
            code,changed,err=self.call('--allow-api','--suite',str(self.root/'suite'))
            self.assertEqual(code,1,err);self.assertFalse(changed['evaluation_valid'])
            self.assertIsNone(changed['quality_pass_rate'])
        self.assertEqual(os.environ.get('DEEPSEEK_API_KEY'),original_key)
        self.assertEqual(len(self.ledger.report()['requests']),4)
