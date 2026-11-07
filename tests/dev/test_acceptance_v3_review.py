"""Independent V3 failure ordering, using only temporary synthetic data."""
from contextlib import contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.budget import BudgetExceeded, BudgetLedger, BudgetedModel
from agent_lab.engine import Engine
from agent_lab.model import ModelError
from agent_lab.native_jobs import JobBlocked
from agent_lab.store import Store
from scripts import evaluate_acceptance_v3 as runner
from scripts.holdout_gate import ExposureBlocked, ExposureGate, ExposureModel
from tests.support import ScriptedModel


class FailingProvider:
    identity = 'synthetic-provider-contract'
    base_url = 'https://api.deepseek.com'
    api_key_env = 'DEEPSEEK_API_KEY'
    model = 'deepseek-flash'
    protocol = 'chat'
    token_parameter = 'max_tokens'
    allow_api = True

    def complete(self, *args, **kwargs):
        raise ModelError('synthetic_transport_unknown', uncertain=True)


class AcceptanceV3ReviewTests(unittest.TestCase):
    def test_reservation_refusal_after_preflight_does_not_consume_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = BudgetLedger(root/'synthetic-budget.sqlite')
            self.assertTrue(runner.budget_preflight(ledger.report(), 'deepseek-flash')['allowed'])
            provider = FailingProvider()
            with ExposureGate(root/'exposures', 'a'*64, 'synthetic_case', 'attempt') as gate:
                model = BudgetedModel(ExposureModel(provider, gate), ledger)
                with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'synthetic-only'}), \
                     patch.object(ledger, 'reserve', side_effect=BudgetExceeded()), \
                     patch.object(provider, 'complete') as delegate:
                    with self.assertRaises(BudgetExceeded):
                        model.complete([], [], 1, 4096)
                delegate.assert_not_called()
                self.assertFalse(gate.exposure_possible)
            self.assertEqual(ledger.report()['requests'], [])
            with ExposureGate(root/'exposures', 'a'*64, 'synthetic_case', 'later'):
                pass

    def test_workspace_gate_refusal_blocks_judging_even_for_terminal_state(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            suite = base/'suite'; source = suite/'project'; source.mkdir(parents=True)
            (source/'solve.py').write_text('def combine(a,b): return a-b\n')
            (source/'test_api.py').write_text('# synthetic immutable original test\n')
            case = {'id':'synthetic_case', 'task':'repair synthetic addition', 'workspace_dir':'project',
                    'immutable_files':['test_api.py'], 'oracle':{'module':'solve', 'function':'combine',
                    'inputs':[[1,2]], 'expected':[3]}}
            original_lock = Store.lock
            locks = []

            @contextmanager
            def gate(store, root):
                locks.append(root)
                if len(locks) == 2:
                    raise JobBlocked()
                with original_lock(store, root):
                    yield

            def stopped(engine, task_id):
                state = engine.store.load(task_id)
                state.update(phase='completed', pending=None, tool_queue=[], outcome_unresolved=False)
                return state

            with patch.object(Store, 'lock', gate), patch.object(Engine, 'run', autospec=True, side_effect=stopped), \
                 patch.object(runner, 'run_tests', return_value={'exit_code':1}) as tests, \
                 patch.object(runner, 'observe_function') as oracle:
                row = runner.run_case_v3(suite, case, base/'trial', ScriptedModel([]))
            self.assertEqual(len(locks), 2)
            self.assertEqual(tests.call_count, 1)  # Baseline only, no candidate judging.
            oracle.assert_not_called()
            self.assertIsNone(row['quality_passed'])
            self.assertIsNone(row['final_public_tests_exit_code'])
            self.assertEqual(row['observation_status'], 'observation_unavailable')

    def test_report_fault_is_fixed_diagnostic_after_key_restore_and_unknown_retention(self):
        for fault in ('report_write', 'budget_report'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ledger = BudgetLedger(root/'synthetic-budget.sqlite')
                snapshot = {'runtime_dirty':False, 'commit':'synthetic', 'source_sha256':{}}
                original_write, original_report = Path.write_text, ledger.report
                report_calls = []

                def report():
                    report_calls.append(True)
                    if fault == 'budget_report' and len(report_calls) == 3:
                        raise OSError('SYNTHETIC_PRIVATE_REPORT_PATH')
                    return original_report()

                def write(path, *args, **kwargs):
                    if fault == 'report_write' and path.name == 'report.json':
                        raise OSError('SYNTHETIC_PRIVATE_REPORT_PATH')
                    return original_write(path, *args, **kwargs)

                def key():
                    os.environ['DEEPSEEK_API_KEY'] = 'synthetic-temporary-key'

                def attempt(suite, case, trial, model, *args, **kwargs):
                    return model.complete([{'role':'user','content':'synthetic input'}], [], 1, 16)

                out = io.StringIO(); error = None
                with patch.dict(os.environ, {'DEEPSEEK_API_KEY':'synthetic-original-key'}), \
                     patch.object(runner, 'REPO', root), \
                     patch.object(runner, 'implementation_snapshot', return_value=snapshot), \
                     patch.object(runner, 'BudgetLedger', return_value=ledger), \
                     patch.object(ledger, 'report', side_effect=report), \
                     patch.object(runner, '_load_suite', return_value=('a'*64,[{'id':'synthetic_case'}])), \
                     patch.object(runner, 'verify_freeze', return_value='a'*64), \
                     patch.object(runner, '_probe_backend', return_value=True), \
                     patch.object(runner, 'load_first_line_key', side_effect=key), \
                     patch.object(runner, 'OpenAIModel', return_value=FailingProvider()), \
                     patch.object(runner, 'run_case_v3', side_effect=attempt), \
                     patch.object(Path, 'write_text', write), redirect_stdout(out):
                    try:
                        code = runner.main(['--allow-api', '--suite', str(root/'synthetic-suite')])
                    except BaseException as caught:
                        error = caught
                    self.assertEqual(os.environ['DEEPSEEK_API_KEY'], 'synthetic-original-key')
                self.assertEqual(original_report()['unresolved_requests'], 1)
                with self.assertRaises(ExposureBlocked):
                    with ExposureGate(root/'.lab'/'acceptance-v3-exposures', 'a'*64, 'synthetic_case', 'retry'):
                        pass
                if error is not None:
                    raise error
                self.assertEqual(code, 2)
                diagnostic = json.loads(out.getvalue())
                self.assertEqual(diagnostic.get('blocked'), 'report_unavailable')
                self.assertNotIn('quality_pass_rate', diagnostic)
                self.assertNotIn('SYNTHETIC_PRIVATE_REPORT_PATH', out.getvalue())
                self.assertNotIn('report', diagnostic)
