"""New prepaid authority changes consumption cap, never route or past records."""
import os
import io
import json
from contextlib import redirect_stdout,redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.budget import BudgetLedger,BudgetedModel,BudgetExceeded
from agent_lab.model import ModelError


class Delegate:
    base_url='https://api.deepseek.com';api_key_env='DEEPSEEK_API_KEY';model='deepseek-flash'
    identity='synthetic_prepaid';allow_api=True;protocol='chat';token_parameter='max_tokens'
    def __init__(self,error=None):self.calls=0;self.error=error
    def complete(self,*args):
        self.calls+=1
        if self.error:raise self.error
        return {'message':{'role':'assistant','content':'synthetic'},'usage':{'input_tokens':100,'output_tokens':20}}


class PrepaidAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'ledger.sqlite'
        env=patch.dict(os.environ,{'DEEPSEEK_API_KEY':'synthetic-never-sent'});env.start();self.addCleanup(env.stop)

    def test_prepaid_uses_existing_records_without_enforcing_legacy_total(self):
        ledger=BudgetLedger(self.path)
        for _ in range(5):ledger.settle(ledger.reserve('deepseek-flash'),None)
        ledger.settle(ledger.reserve('deepseek-flash',32),None)
        before=ledger.report()['requests']
        with self.assertRaises(BudgetExceeded):BudgetedModel(Delegate(),ledger).complete([],[],1,32)
        delegate=Delegate();result=BudgetedModel(delegate,ledger,prepaid_account=True).complete([],[],1,32)
        self.assertEqual(result['usage']['input_tokens'],100);self.assertEqual(delegate.calls,1)
        report=ledger.report();self.assertEqual(report['requests'][:6],before);self.assertEqual(report['unresolved_requests'],6)
        self.assertEqual(len(report['requests']),7)

    def test_402_stops_further_calls_and_persists_across_new_wrapper(self):
        ledger=BudgetLedger(self.path);delegate=Delegate(ModelError('http_error',http_status=402))
        with self.assertRaisesRegex(ModelError,'account_balance_exhausted'):
            BudgetedModel(delegate,ledger,prepaid_account=True).complete([],[],1,32)
        self.assertEqual(delegate.calls,1)
        later=Delegate()
        with self.assertRaises(ModelError):BudgetedModel(later,BudgetLedger(self.path),prepaid_account=True).complete([],[],1,32)
        self.assertEqual(later.calls,0);self.assertTrue(ledger.report()['halted'])
        self.assertEqual(ledger.report()['requests'][0]['status'],'unknown')

    def test_cli_prepaid_flag_reaches_existing_ledger_and_retains_key_admission(self):
        from agent_lab.__main__ import main
        from tests.dev.test_budget_routing_cli import NonNetworkModel
        ledger=BudgetLedger(self.path)
        for _ in range(5):ledger.settle(ledger.reserve('deepseek-flash'),None)
        ledger.settle(ledger.reserve('deepseek-flash',32),None)
        root=Path(self.temp.name)/'work';root.mkdir();out,err=io.StringIO(),io.StringIO();NonNetworkModel.calls=[]
        def load():os.environ['DEEPSEEK_API_KEY']='synthetic-loaded'
        args=['--db',str(Path(self.temp.name)/'state.sqlite'),'run','Synthetic prepaid CLI','--workspace',str(root),
              '--model','deepseek-flash','--base-url','https://api.deepseek.com','--api-key-env','DEEPSEEK_API_KEY',
              '--allow-api','--load-deepseek-key','--budget-ledger',str(self.path),'--prepaid-account']
        with patch('agent_lab.__main__.OpenAIModel',NonNetworkModel),patch('agent_lab.budget.load_first_line_key',side_effect=load) as loader,redirect_stdout(out),redirect_stderr(err):
            code=main(args)
        self.assertEqual(code,0,err.getvalue());self.assertEqual(NonNetworkModel.calls,[True]);loader.assert_called_once()
        self.assertEqual(ledger.report()['unresolved_requests'],6)
        args[args.index('DEEPSEEK_API_KEY')]='OTHER_FAKE_KEY'
        with patch('agent_lab.__main__.OpenAIModel',NonNetworkModel),patch('agent_lab.budget.load_first_line_key') as loader,redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(main(args),2);loader.assert_not_called()

    def test_v3_prepaid_preflight_never_opens_task_or_key_without_allow_api(self):
        from scripts import evaluate_acceptance_v3 as runner
        ledger=BudgetLedger(self.path)
        for _ in range(5):ledger.settle(ledger.reserve('deepseek-flash'),None)
        ledger.settle(ledger.reserve('deepseek-flash',32),None)
        out=io.StringIO()
        with patch.object(runner,'implementation_snapshot',return_value={'runtime_dirty':False}),patch.object(runner,'BudgetLedger',return_value=ledger),patch.object(runner,'_load_suite') as suite,patch.object(runner,'load_first_line_key') as key,redirect_stdout(out):
            code=runner.main(['--prepaid-account'])
        self.assertEqual(code,0);self.assertTrue(json.loads(out.getvalue())['budget_allows_request']);suite.assert_not_called();key.assert_not_called()
        self.assertFalse(runner.budget_preflight(ledger.report(),'deepseek-flash')['allowed'])

    def test_halted_prepaid_refusal_preserves_known_usage_in_decision_and_auto_summary(self):
        from agent_lab.coding_tools import CodingTools
        from agent_lab.engine import Engine,Limits
        from agent_lab.store import Store
        from tests.dev.test_history import pair
        ledger=BudgetLedger(self.path);ledger.halt_paid_requests();root=Path(self.temp.name)/'work';root.mkdir()
        for automatic in (False,True):
            with self.subTest(automatic=automatic),Store(Path(self.temp.name)/('state-'+str(automatic)+'.sqlite')) as store:
                delegate=Delegate();engine=Engine(store,CodingTools(root),BudgetedModel(delegate,ledger,prepaid_account=True))
                task=engine.create('Synthetic halted account',Limits(input_price=.1,output_price=.2,auto_compact_bytes=1 if automatic else None,max_context_bytes=32000))
                if automatic:
                    state=store.load(task);state['messages']+=[{'role':'assistant','content':'prior context '*400}]
                    state['messages']+=pair('old','read_file',{'path':'file.py'},{'ok':True,'content':'old'})
                    state['messages']+=pair('latest','read_file',{'path':'file.py'},{'ok':True,'content':'latest'})
                    store.save(state,'synthetic_fixture')
                state=engine.run(task)
                self.assertEqual(state['phase'],'failed');self.assertEqual(state['reason'],'paid_requests_halted')
                self.assertFalse(state.get('usage_unknown',False));self.assertEqual(delegate.calls,0)
                self.assertEqual(state['metrics']['input_tokens'],0);self.assertEqual(state['metrics']['output_tokens'],0)
                self.assertEqual(state['metrics']['cost_usd'],0.0)

    def test_prepaid_is_explicit_bool_and_does_not_relax_endpoint_or_key_guards(self):
        ledger=BudgetLedger(self.path)
        for value in (1,'yes',None):
            with self.subTest(value=value),self.assertRaises(ValueError):BudgetedModel(Delegate(),ledger,prepaid_account=value)
        delegate=Delegate();delegate.base_url='https://example.invalid'
        with self.assertRaises(ValueError):BudgetedModel(delegate,ledger,prepaid_account=True)
        delegate=Delegate();delegate.allow_api=False
        with self.assertRaisesRegex(ModelError,'api_not_enabled'):BudgetedModel(delegate,ledger,prepaid_account=True).complete([],[],1,32)
        self.assertEqual(delegate.calls,0);self.assertEqual(ledger.report()['requests'],[])


if __name__=='__main__':unittest.main()
