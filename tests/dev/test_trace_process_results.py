import json
from pathlib import Path
import tempfile
import unittest

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final
from agent_lab.model import ModelError


class ProcessTraceTests(unittest.TestCase):
    def test_transport_metadata_is_bounded_and_unknown_usage_stays_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir()
            error=ModelError('transport_unknown',uncertain=True,http_status=200,
                             diagnostic=['phase_body','incomplete_read','PRIVATE_ERROR_TEXT'],response_body_bytes=17)
            with Store(Path(temp)/'state.sqlite') as store:
                engine=Engine(store,CodingTools(root),ScriptedModel([error]))
                tid=engine.create('Task');state=engine.run(tid)
                event=next(e for e in store.events(tid) if e['kind']=='model_error')
                self.assertEqual(event['http_status'],200)
                self.assertEqual(event['response_body_bytes'],17)
                self.assertEqual(event['diagnostic'],'phase_body,incomplete_read')
                self.assertNotIn('PRIVATE_ERROR_TEXT',json.dumps(store.events(tid)))
                self.assertIsNone(state['metrics']['input_tokens'])
                self.assertEqual(state['phase'],'needs_confirmation')

    def test_local_budget_refusal_does_not_erase_known_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir()
            with Store(Path(temp)/'state.sqlite') as store:
                engine=Engine(store,CodingTools(root),ScriptedModel([ModelError('budget_exhausted')]))
                state=engine.run(engine.create('Task'))
                self.assertEqual(state['reason'],'budget_exhausted')
                self.assertEqual(state['metrics']['input_tokens'],0)
                self.assertEqual(state['metrics']['output_tokens'],0)
                self.assertNotIn('usage_unknown',state)

    def test_failed_process_exit_is_visible_without_private_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir()
            marker='PRIVATE_TEST_OUTPUT_NOT_FOR_TRACE'
            (root/'test_failure.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_fail(self):\n  print('+repr(marker)+')\n  self.fail('+repr(marker)+')\n')
            with Store(Path(temp)/'state.sqlite') as store:
                engine=Engine(store,CodingTools(root),ScriptedModel([tool('run_tests',{}),final('done')]))
                tid=engine.create('Run the tests');state=engine.run(tid)
                events=store.events(tid)
                event=next(e for e in events if e['kind']=='tool_finished' and e.get('tool')=='run_tests')
                self.assertFalse(event['ok'])
                self.assertEqual(event['exit_code'],1)
                self.assertEqual(event['code'],'process_exit_nonzero')
                self.assertNotIn(marker,json.dumps(events))
                self.assertIn(marker,json.dumps(state['messages']))
                self.assertIsNone(state['verified_success'])
