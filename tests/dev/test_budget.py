"""A shared paid-request budget is a permission boundary, not a usage counter."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

try:
    from agent_lab.budget import BudgetLedger, BudgetExceeded, BudgetedModel, load_first_line_key
except ImportError:
    BudgetLedger = None


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'budget.sqlite'
        fake_environment = patch.dict(os.environ, {'DEEPSEEK_API_KEY':'fake-budget-key'})
        fake_environment.start(); self.addCleanup(fake_environment.stop)

    def ledger(self):
        self.assertIsNotNone(BudgetLedger, 'persistent paid budget is missing')
        return BudgetLedger(self.path)

    def test_reservations_survive_restart_and_cannot_overspend(self):
        ledger = self.ledger()
        ids = [ledger.reserve('deepseek-flash') for _ in range(5)]
        with self.assertRaises(BudgetExceeded):
            BudgetLedger(self.path).reserve('deepseek-flash')
        report = ledger.report()
        self.assertEqual(len(report['requests']), 5)
        self.assertLessEqual(report['committed_upper_cny'], 30)
        # An unknown response never releases its reservation.
        ledger.settle(ids[0], None, 'model_timeout')
        self.assertEqual(ledger.report()['requests'][0]['status'], 'unknown')
        with self.assertRaises(BudgetExceeded):
            ledger.reserve('deepseek-flash')

    def test_known_usage_releases_only_unused_reservation(self):
        ledger = self.ledger()
        rid = ledger.reserve('deepseek-flash')
        ledger.settle(rid, {'input_tokens': 1000, 'output_tokens': 500})
        report = BudgetLedger(self.path).report()
        self.assertAlmostEqual(report['committed_upper_cny'], .006)
        self.assertEqual(report['requests'][0]['usage'], {'input_tokens': 1000, 'output_tokens': 500})
        # Settlement replay is safe; conflicting acknowledgement must be rejected.
        ledger.settle(rid, {'input_tokens': 1000, 'output_tokens': 500})
        with self.assertRaises(ValueError):
            ledger.settle(rid, {'input_tokens': 0, 'output_tokens': 0})

    def test_invalid_usage_does_not_grant_free_requests(self):
        for usage in ({'input_tokens': -1, 'output_tokens': 0}, {}, {'input_tokens': True, 'output_tokens': 1}):
            with self.subTest(usage=usage):
                ledger = self.ledger()
                rid = ledger.reserve('deepseek-flash')
                ledger.settle(rid, usage)
                self.assertEqual(ledger.report()['requests'][-1]['status'], 'unknown')

    def test_concurrent_reservation_is_serialized_by_sqlite(self):
        from concurrent.futures import ThreadPoolExecutor
        ledger = self.ledger()
        def reserve(_):
            try:
                return BudgetLedger(self.path).reserve('deepseek-v4-pro')
            except BudgetExceeded:
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(reserve, range(4)))
        self.assertEqual(sum(x is not None for x in outcomes), 1)
        self.assertLessEqual(ledger.report()['committed_upper_cny'], 30)

    def test_credential_loader_consumes_only_first_line_and_never_persists(self):
        self.assertIsNotNone(BudgetLedger, 'persistent paid budget is missing')
        source = Path(self.tmp.name) / 'synthetic.txt'
        source.write_text('  fake-key  \nDO_NOT_READ_SECOND_LINE\n')
        with patch.dict(os.environ, {}, clear=True):
            load_first_line_key(source)
            self.assertEqual(os.environ['DEEPSEEK_API_KEY'], 'fake-key')
        self.assertEqual(source.read_text(), '  fake-key  \nDO_NOT_READ_SECOND_LINE\n')

    def test_nonofficial_endpoint_rejected_before_delegate_and_reservation(self):
        ledger = self.ledger()
        class Model:
            base_url = 'https://api.deepseek.com.attacker.invalid'
            api_key_env = 'DEEPSEEK_API_KEY'
            model = 'deepseek-flash'
            identity = 'example'
            def complete(self, *args):
                raise AssertionError('must not call')
        with self.assertRaises(ValueError):
            BudgetedModel(Model(), ledger)
        self.assertEqual(ledger.report()['requests'], [])

    def test_model_failure_keeps_reservation_and_error_text_out_of_ledger(self):
        from agent_lab.model import ModelError
        ledger = self.ledger()
        class Model:
            base_url = 'https://api.deepseek.com'
            api_key_env = 'DEEPSEEK_API_KEY'
            model = 'deepseek-flash'
            identity = 'example'
            def complete(self, *args):
                raise ModelError('model_timeout', uncertain=True)
        with self.assertRaises(ModelError):
            BudgetedModel(Model(), ledger).complete([], [], 1, 50)
        report = ledger.report()
        self.assertEqual(report['requests'][0]['status'], 'unknown')
        self.assertGreater(report['committed_upper_cny'], 5)
        self.assertNotIn('fake-key', json.dumps(report))

    def test_budget_configuration_is_not_reset_by_reopening(self):
        ledger = self.ledger()
        ledger.reserve('deepseek-flash')
        # The fixed user authorization is recorded and validated, not overwritten.
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE budget SET cap_micros=1')
        with self.assertRaises(ValueError):
            BudgetLedger(self.path)

    def test_provider_overrun_latches_budget_closed_across_restart(self):
        from agent_lab.model import ModelError
        ledger = self.ledger()
        rid = ledger.reserve('deepseek-flash')
        with self.assertRaises(ModelError):
            ledger.settle(rid, {'input_tokens': 2_000_000, 'output_tokens': 200_000})
        with self.assertRaises(BudgetExceeded):
            BudgetLedger(self.path).reserve('deepseek-flash')

    def test_endpoint_cannot_be_changed_after_wrapper_creation(self):
        ledger = self.ledger()
        class Model:
            base_url = 'https://api.deepseek.com'
            api_key_env = 'DEEPSEEK_API_KEY'
            model = 'deepseek-flash'
            identity = 'example'
            def complete(self, *args):
                raise AssertionError('no request may reach changed endpoint')
        delegate = Model()
        model = BudgetedModel(delegate, ledger)
        delegate.base_url = 'https://attacker.invalid'
        with self.assertRaises(ValueError):
            model.complete([], [], 1, 10)
        self.assertEqual(ledger.report()['requests'], [])

    def test_documented_chat_output_cap_can_use_remaining_authorization_without_reset(self):
        ledger = self.ledger()
        for _ in range(5):
            ledger.settle(ledger.reserve('deepseek-flash'), None)
        class Model:
            base_url='https://api.deepseek.com'
            api_key_env='DEEPSEEK_API_KEY'
            model='deepseek-flash'
            identity='explicit_chat_max_tokens'
            protocol='chat'
            token_parameter='max_tokens'
            def complete(self,*args):
                return {'message':{'role':'assistant','content':'ok'},'usage':{'input_tokens':100,'output_tokens':20}}
        BudgetedModel(Model(),ledger).complete([],[],1,128)
        report=ledger.report()
        self.assertEqual(report['unresolved_requests'],5)
        self.assertAlmostEqual(report['committed_upper_cny'],26.21476)
        self.assertAlmostEqual(report['requests'][-1]['reserved_cny'],2.098176)
        self.assertEqual(report['requests'][-1]['output_token_bound'],128)

    def test_known_local_preflight_failure_does_not_consume_paid_reservation(self):
        from agent_lab.model import OpenAIModel,ModelError
        ledger=self.ledger()
        for allow,missing in ((False,False),(True,True)):
            delegate=OpenAIModel('deepseek-flash','https://api.deepseek.com','DEEPSEEK_API_KEY',allow,token_parameter='max_tokens')
            with patch.dict(os.environ,{},clear=True):
                if not missing:os.environ['DEEPSEEK_API_KEY']='fake-budget-key'
                with self.assertRaises(ModelError):BudgetedModel(delegate,ledger).complete([],[],1,32)
            self.assertEqual(ledger.report()['requests'],[])
